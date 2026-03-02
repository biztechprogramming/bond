/**
 * WebChat channel — handles messages from the web frontend via WebSocket.
 *
 * History is now managed server-side. The gateway forwards conversation_id
 * between frontend and backend.
 */

import type { WebSocket } from "ws";
import type { IncomingMessage, OutgoingMessage } from "../protocol/types.js";
import type { SessionManager } from "../sessions/manager.js";
import type { BackendClient, AgentResolution } from "../backend/client.js";
import { WorkerPool } from "../backend/worker-pool.js";
import type { WorkerSSEEvent } from "../backend/worker-client.js";

export class WebChatChannel {
  private workerPool = new WorkerPool();

  constructor(
    private sessionManager: SessionManager,
    private backendClient: BackendClient
  ) {}

  async handleConnection(socket: WebSocket): Promise<void> {
    const session = this.sessionManager.createSession();
    this.sessionManager.registerClient(socket, session.id);

    // Send connected event with session info
    this.send(socket, {
      type: "connected",
      sessionId: session.id,
    });

    socket.on("message", async (data) => {
      try {
        const msg: IncomingMessage = JSON.parse(data.toString());
        await this.handleMessage(socket, msg);
      } catch (err) {
        this.send(socket, {
          type: "error",
          error: err instanceof Error ? err.message : "Unknown error",
        });
      }
    });

    socket.on("close", () => {
      this.sessionManager.removeClient(socket);
    });
  }

  private async handleMessage(
    socket: WebSocket,
    msg: IncomingMessage
  ): Promise<void> {
    const client = this.sessionManager.getClient(socket);
    if (!client) return;

    const session = this.sessionManager.getSession(client.sessionId);
    if (!session) return;

    switch (msg.type) {
      case "message":
        await this.handleChatMessage(socket, session.id, msg);
        break;
      case "interrupt":
        await this.handleInterrupt(socket, session.id, msg);
        break;
      case "switch_conversation":
        await this.handleSwitchConversation(socket, session.id, msg);
        break;
      case "new_conversation":
        this.sessionManager.setConversationId(session.id, "");
        session.conversationId = null;
        session.agentBusy = false;
        this.send(socket, {
          type: "connected",
          sessionId: session.id,
          conversationId: undefined,
        });
        break;
      case "list_conversations":
        await this.handleListConversations(socket);
        break;
      case "delete_conversation":
        await this.handleDeleteConversation(socket, session.id, msg);
        break;
    }
  }

  private async handleChatMessage(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    if (!msg.content) return;

    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId || undefined;

    if (session.agentBusy && conversationId) {
      // Agent is busy — queue the message in DB
      try {
        const queueResult = await this.backendClient.queueMessage(conversationId, msg.content);
        this.send(socket, {
          type: "queued",
          sessionId,
          messageId: queueResult.message_id,
          queuePosition: queueResult.queue_position,
          conversationId,
        });
      } catch (err) {
        this.send(socket, {
          type: "error",
          sessionId,
          error: err instanceof Error ? err.message : "Failed to queue message",
        });
      }
      return;
    }

    // Agent is idle — start a new turn with SSE streaming
    await this.startStreamingTurn(socket, sessionId, msg.content, conversationId, msg.agentId);
  }

  private async handleInterrupt(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId;
    if (!conversationId) return;

    try {
      const resolution = await this.backendClient.resolveAgent(conversationId);

      if (resolution.mode === "container" && resolution.worker_url) {
        const worker = this.workerPool.get(resolution.worker_url);
        await worker.interrupt([]);
      } else {
        await this.backendClient.interrupt(conversationId);
      }

      this.send(socket, {
        type: "status",
        sessionId,
        agentStatus: "idle",
        conversationId,
      });
    } catch (err) {
      this.send(socket, {
        type: "error",
        sessionId,
        error: err instanceof Error ? err.message : "Failed to interrupt",
      });
    }
  }

  private async startStreamingTurn(
    socket: WebSocket,
    sessionId: string,
    message: string | undefined,
    conversationId: string | undefined,
    agentId?: string,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    try {
      // Resolve agent mode — use explicit agentId if provided, otherwise default
      const resolution = await this.backendClient.resolveAgent(conversationId, agentId || (conversationId ? undefined : "default"));
      console.log(
        `[gateway] Resolving agent for conversation ${resolution.conversation_id} → ${resolution.mode}` +
        (resolution.worker_url ? ` (worker ${resolution.worker_url})` : ""),
      );

      if (resolution.mode === "container" && resolution.worker_url) {
        await this.startContainerTurn(socket, sessionId, message, resolution);
      } else {
        await this.startHostTurn(socket, sessionId, message, resolution);
      }
    } catch (err) {
      this.send(socket, {
        type: "error",
        sessionId,
        error: err instanceof Error ? err.message : "Failed to resolve agent",
      });
    }
  }

  private async startHostTurn(
    socket: WebSocket,
    sessionId: string,
    message: string | undefined,
    resolution: import("../backend/client.js").AgentResolution,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = resolution.conversation_id;
    const agentName = resolution.agent_display_name || resolution.agent_name || "Agent";

    session.agentBusy = true;
    this.send(socket, {
      type: "status",
      sessionId,
      agentStatus: "thinking",
      agentName,
      conversationId,
    });

    try {
      let responseContent = "";
      let responseConversationId = conversationId || "";
      let responseMessageId = "";
      let queuedCount = 0;

      for await (const event of this.backendClient.agentTurnStream({
        message: message || undefined,
        conversation_id: conversationId,
      })) {
        switch (event.event) {
          case "status":
            this.send(socket, {
              type: "status",
              sessionId,
              agentStatus: event.data.state as "thinking" | "tool_calling" | "responding",
              conversationId: (event.data.conversation_id as string) || responseConversationId,
            });
            if (event.data.conversation_id) {
              responseConversationId = event.data.conversation_id as string;
            }
            break;
          case "chunk":
            responseContent += (event.data.content as string) || "";
            this.send(socket, {
              type: "chunk",
              sessionId,
              content: event.data.content as string,
              agentName,
              conversationId: responseConversationId,
            });
            break;
          case "new_input":
            this.send(socket, {
              type: "new_input",
              sessionId,
              conversationId: responseConversationId,
              queuedCount: event.data.count as number,
            });
            break;
          case "done":
            responseMessageId = (event.data.message_id as string) || "";
            responseConversationId = (event.data.conversation_id as string) || responseConversationId;
            queuedCount = (event.data.queued_count as number) || 0;
            break;
        }
      }

      // Store conversation ID in session
      if (responseConversationId) {
        this.sessionManager.setConversationId(sessionId, responseConversationId);
        session.conversationId = responseConversationId;
      }

      session.agentBusy = false;

      // Send done + full response
      this.send(socket, {
        type: "done",
        sessionId,
        conversationId: responseConversationId,
        messageId: responseMessageId,
        agentName,
        queuedCount,
        agentStatus: "idle",
      });

      // Refresh conversation list
      this.handleListConversations(socket).catch(() => {});

      // Auto-continue if there are queued messages
      if (queuedCount > 0) {
        setTimeout(() => {
          this.startStreamingTurn(socket, sessionId, undefined, responseConversationId);
        }, 500);
      }
    } catch (err) {
      session.agentBusy = false;
      this.send(socket, {
        type: "status",
        sessionId,
        agentStatus: "idle",
        conversationId,
      });
      const isAbort = err instanceof Error && (err.name === "AbortError" || err.message.includes("aborted"));
      this.send(socket, {
        type: "error",
        sessionId,
        error: isAbort
          ? "The agent took too long to respond and the request timed out. Try a simpler request or check the agent logs."
          : err instanceof Error ? err.message : "Agent error",
      });
    }
  }

  private async startContainerTurn(
    socket: WebSocket,
    sessionId: string,
    message: string | undefined,
    resolution: AgentResolution,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = resolution.conversation_id;
    const workerUrl = resolution.worker_url!;
    const agentName = resolution.agent_display_name || resolution.agent_name || "Agent";
    const startTime = Date.now();

    session.agentBusy = true;
    this.send(socket, {
      type: "status",
      sessionId,
      agentStatus: "thinking",
      agentName,
      conversationId,
    });

    console.log(`[gateway] Starting container turn: conversation=${conversationId} worker=${workerUrl} agent=${agentName}`);

    try {
      // Load conversation history from backend
      const conv = await this.backendClient.getConversation(conversationId);
      const messages = conv.messages.map((m) => ({ role: m.role, content: m.content }));
      if (message) {
        messages.push({ role: "user", content: message });
      }

      // Persist user message before sending to worker
      if (message) {
        try {
          await this.backendClient.saveUserMessage(conversationId, message);
        } catch (err) {
          console.error(
            `[gateway] ERROR Failed to save user message: conversation=${conversationId} error=${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }

      const worker = this.workerPool.get(workerUrl);

      // Apply turn timeout from settings
      try {
        const timeoutSetting = await this.backendClient.getSetting("agent.turn_timeout_minutes");
        if (timeoutSetting) {
          const ms = parseInt(timeoutSetting, 10) * 60_000;
          if (ms > 0) worker.setTurnTimeout(ms);
        }
      } catch { /* use default */ }

      let responseContent = "";
      let toolCallsMade = 0;

      for await (const event of worker.turnStream({
        messages,
        conversation_id: conversationId,
      })) {
        switch (event.event) {
          case "status":
            console.log(`[gateway] Container turn SSE event: status ${event.data.state}`);
            this.send(socket, {
              type: "status",
              sessionId,
              agentStatus: event.data.state as "thinking" | "tool_calling" | "responding",
              conversationId,
            });
            break;

          case "chunk":
            responseContent += (event.data.content as string) || "";
            this.send(socket, {
              type: "chunk",
              sessionId,
              content: event.data.content as string,
              agentName,
              conversationId,
            });
            break;

          case "tool_call":
            this.send(socket, {
              type: "tool_call",
              sessionId,
              content: JSON.stringify(event.data),
              conversationId,
            });
            break;

          case "plan_created":
            console.log(`[gateway] Container turn SSE event: plan_created id=${event.data.plan_id}`);
            this.broadcast({
              type: "plan_created",
              sessionId,
              planId: event.data.plan_id as string,
              planTitle: event.data.title as string,
              planStatus: "active",
              conversationId,
            });
            break;

          case "item_created":
            console.log(`[gateway] Container turn SSE event: item_created plan=${event.data.plan_id} item=${event.data.item_id}`);
            this.broadcast({
              type: "item_updated",
              sessionId,
              planId: event.data.plan_id as string,
              itemId: event.data.item_id as string,
              itemStatus: "new",
              itemTitle: (event.data.title as string) || "",
              conversationId,
            });
            break;

          case "item_updated":
            console.log(`[gateway] Container turn SSE event: item_updated plan=${event.data.plan_id} item=${event.data.item_id} status=${event.data.status}`);
            this.broadcast({
              type: "item_updated",
              sessionId,
              planId: event.data.plan_id as string,
              itemId: event.data.item_id as string,
              itemStatus: event.data.status as string,
              itemTitle: (event.data.title as string) || "",
              conversationId,
            });
            break;

          case "plan_completed":
            console.log(`[gateway] Container turn SSE event: plan_completed id=${event.data.plan_id} status=${event.data.status}`);
            this.broadcast({
              type: "plan_completed",
              sessionId,
              planId: event.data.plan_id as string,
              planStatus: event.data.status as string,
              conversationId,
            });
            break;

          case "memory":
            console.log(`[gateway] Container turn SSE event: memory promote type=${event.data.type}`);
            // Intercept: forward to backend, do NOT forward to frontend
            this.backendClient.promoteMemory({
              agent_id: resolution.agent_id,
              memory_id: event.data.memory_id as string,
              type: event.data.type as string,
              content: event.data.content as string,
              summary: (event.data.summary as string) || "",
              source_type: "agent",
              entities: (event.data.entities as string[]) || [],
            }).then(() => {
              console.log(`[gateway] Memory promotion sent to backend: agent=${resolution.agent_id} type=${event.data.type}`);
            }).catch((err) => {
              console.warn(
                `[gateway] WARN Memory promotion failed (non-fatal): agent=${resolution.agent_id} error=${err instanceof Error ? err.message : String(err)}`,
              );
            });
            break;

          case "done":
            responseContent = (event.data.response as string) || responseContent;
            toolCallsMade = (event.data.tool_calls_made as number) || 0;
            break;

          case "error":
            this.send(socket, {
              type: "error",
              sessionId,
              error: event.data.message as string,
              conversationId,
            });
            break;
        }
      }

      // Save assistant message to backend
      let responseMessageId = "";
      try {
        const saveResult = await this.backendClient.saveAssistantMessage(
          conversationId,
          responseContent,
          toolCallsMade,
        );
        responseMessageId = saveResult.message_id;
        console.log(`[gateway] Assistant message saved: conversation=${conversationId} message_id=${responseMessageId}`);
      } catch (err) {
        console.error(
          `[gateway] ERROR Failed to save assistant message: conversation=${conversationId} error=${err instanceof Error ? err.message : String(err)}`,
        );
      }

      // Update session state
      if (conversationId) {
        this.sessionManager.setConversationId(sessionId, conversationId);
        session.conversationId = conversationId;
      }

      session.agentBusy = false;
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      console.log(
        `[gateway] Container turn complete: conversation=${conversationId} tool_calls=${toolCallsMade} response_length=${responseContent.length} elapsed=${elapsed}s`,
      );

      this.send(socket, {
        type: "done",
        sessionId,
        conversationId,
        messageId: responseMessageId,
        agentName,
        queuedCount: 0,
        agentStatus: "idle",
      });

      // Refresh conversation list
      this.handleListConversations(socket).catch(() => {});
    } catch (err) {
      session.agentBusy = false;
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      const errorMsg = err instanceof Error ? err.message : "Container turn error";
      console.error(
        `[gateway] ERROR Container turn failed: conversation=${conversationId} error=${errorMsg} elapsed=${elapsed}s`,
      );

      // Persist error as assistant message so it shows in history
      try {
        await this.backendClient.saveAssistantMessage(
          conversationId,
          `Error: ${errorMsg}`,
          0,
        );
      } catch (saveErr) {
        console.error(
          `[gateway] ERROR Failed to save error message: ${saveErr instanceof Error ? saveErr.message : String(saveErr)}`,
        );
      }

      this.send(socket, {
        type: "status",
        sessionId,
        agentStatus: "idle",
        conversationId,
      });
      const isTimeout = errorMsg === "terminated" || errorMsg.includes("aborted");
      this.send(socket, {
        type: "error",
        sessionId,
        error: isTimeout
          ? "The agent is still working but the request timed out. The agent's work is preserved — check the files it was writing. You can increase the turn timeout in Settings."
          : errorMsg,
      });
    }
  }

  private async handleSwitchConversation(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    if (!msg.conversationId) return;

    try {
      const conv = await this.backendClient.getConversation(msg.conversationId);
      this.sessionManager.setConversationId(sessionId, msg.conversationId);

      this.send(socket, {
        type: "history",
        sessionId,
        conversationId: msg.conversationId,
        messages: conv.messages.map((m) => ({
          role: m.role,
          content: m.content,
          id: m.id,
          created_at: m.created_at,
        })),
      });
    } catch (err) {
      this.send(socket, {
        type: "error",
        sessionId,
        error: err instanceof Error ? err.message : "Failed to load conversation",
      });
    }
  }

  private async handleListConversations(socket: WebSocket): Promise<void> {
    try {
      const conversations = await this.backendClient.listConversations();
      this.send(socket, {
        type: "conversations_list",
        conversations,
      });
    } catch (err) {
      this.send(socket, {
        type: "error",
        error: err instanceof Error ? err.message : "Failed to list conversations",
      });
    }
  }

  private async handleDeleteConversation(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    if (!msg.conversationId) return;

    try {
      await this.backendClient.deleteConversation(msg.conversationId);

      // If we deleted the active conversation, clear it
      const session = this.sessionManager.getSession(sessionId);
      if (session?.conversationId === msg.conversationId) {
        session.conversationId = null;
      }

      // Refresh conversation list
      await this.handleListConversations(socket);
    } catch (err) {
      this.send(socket, {
        type: "error",
        error: err instanceof Error ? err.message : "Failed to delete conversation",
      });
    }
  }

  private broadcast(msg: OutgoingMessage): void {
    const payload = JSON.stringify(msg);
    for (const socket of this.sessionManager.getAllSockets()) {
      if (socket.readyState === socket.OPEN) {
        socket.send(payload);
      }
    }
  }

  private send(socket: WebSocket, msg: OutgoingMessage): void {
    if (socket.readyState === socket.OPEN) {
      socket.send(JSON.stringify(msg));
    }
  }
}
