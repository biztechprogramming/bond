/**
 * WebChat channel — handles messages from the web frontend via WebSocket.
 *
 * Message handling is delegated to the pipeline. Non-message WS events
 * (switch_conversation, list_conversations, etc.) are handled directly.
 */

import { ulid } from "ulid";
import type { WebSocket } from "ws";
import type { IncomingMessage, OutgoingMessage } from "../protocol/types.js";
import type { SessionManager } from "../sessions/manager.js";
import type { BackendClient } from "../backend/client.js";
import type { MessagePipeline, PipelineContext, PipelineMessage } from "../pipeline/index.js";

export class WebChatChannel {
  /** Accumulated streamed content per conversation during an active turn. */
  private streamBuffers = new Map<string, { content: string; agentName: string; agentStatus: string }>();

  private pipeline: MessagePipeline | null = null;

  constructor(
    private sessionManager: SessionManager,
    private backendClient: BackendClient,
  ) {}

  /** Set the pipeline for message processing. */
  setPipeline(pipeline: MessagePipeline): void {
    this.pipeline = pipeline;
  }

  async handleConnection(socket: WebSocket): Promise<void> {
    const session = this.sessionManager.createSession();
    this.sessionManager.registerClient(socket, session.id);

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
    msg: IncomingMessage,
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
      case "pause":
        await this.handlePause(socket, session.id, msg);
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
      case "ping":
        this.send(socket, { type: "pong" } as OutgoingMessage);
        break;
    }
  }

  private async handleChatMessage(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage,
  ): Promise<void> {
    if (!msg.content) return;

    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId || undefined;

    if (session.agentBusy && conversationId) {
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

    // Resolve conversation ID upfront
    const resolvedConversationId = conversationId || ulid();

    // Echo user message to other sockets watching this conversation
    const otherSockets = this.sessionManager.getSocketsForConversation(resolvedConversationId)
      .filter((s) => s !== socket);
    if (otherSockets.length > 0) {
      const echoMsg = JSON.stringify({
        type: "user_message",
        content: msg.content,
        conversationId: resolvedConversationId,
      });
      for (const s of otherSockets) {
        s.send(echoMsg);
      }
    }

    if (this.pipeline) {
      await this.executePipeline(socket, sessionId, msg, resolvedConversationId);
    } else {
      await this.startTurn(socket, sessionId, msg.content, resolvedConversationId, msg.agentId, msg.planId);
    }
  }

  /**
   * Execute the message through the pipeline.
   */
  private async executePipeline(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage,
    conversationId: string,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const startTime = Date.now();
    session.agentBusy = true;

    this.sessionManager.setConversationId(sessionId, conversationId);
    session.conversationId = conversationId;

    this.streamBuffers.set(conversationId, { content: "", agentName: "", agentStatus: "thinking" });

    this.sendToConversation(conversationId, {
      type: "status",
      sessionId,
      agentStatus: "thinking",
      conversationId,
    });

    const pipelineMessage: PipelineMessage = {
      id: ulid(),
      channelType: "webchat",
      channelId: sessionId,
      content: msg.content!,
      conversationId,
      agentId: msg.agentId,
      timestamp: Date.now(),
      metadata: {
        agentId: msg.agentId,
        planId: msg.planId,
        conversationId,
      },
    };

    const context: PipelineContext = {
      aborted: false,

      respond: async (text: string) => {
        this.sendToConversation(conversationId, {
          type: "error",
          sessionId,
          error: text,
          conversationId,
        });
      },

      broadcast: async (_text: string) => {
        // Handled by ResponseFanOut
      },

      streamChunk: async (chunk: string) => {
        const buf = this.streamBuffers.get(conversationId);
        if (buf) buf.content += chunk;
        this.sendToConversation(conversationId, {
          type: "chunk",
          sessionId,
          content: chunk,
          agentName: pipelineMessage.agentName || buf?.agentName,
          conversationId,
        });
      },

      abort: async (reason: string) => {
        context.aborted = true;
        this.sendToConversation(conversationId, {
          type: "error",
          sessionId,
          error: reason,
          conversationId,
        });
      },

      emit: async (event: string, data: Record<string, any>) => {
        switch (event) {
          case "status": {
            const buf = this.streamBuffers.get(conversationId);
            if (buf) {
              buf.agentStatus = data.agentStatus;
              if (data.agentName) buf.agentName = data.agentName;
            }
            this.sendToConversation(conversationId, {
              type: "status",
              sessionId,
              agentStatus: data.agentStatus,
              conversationId,
            });
            break;
          }
          case "tool_call":
            this.sendToConversation(conversationId, {
              type: "tool_call",
              sessionId,
              content: data.content,
              conversationId,
            });
            break;
          case "plan_created":
            this.broadcast({
              type: "plan_created", sessionId,
              planId: data.planId, planTitle: data.planTitle,
              planStatus: "active", conversationId,
            });
            break;
          case "item_updated":
            this.broadcast({
              type: "item_updated", sessionId,
              planId: data.planId, itemId: data.itemId,
              itemStatus: data.itemStatus, itemTitle: data.itemTitle,
              conversationId,
            });
            break;
          case "plan_completed":
            this.broadcast({
              type: "plan_completed", sessionId,
              planId: data.planId, planStatus: data.planStatus,
              conversationId,
            });
            break;
          case "error":
            this.sendToConversation(conversationId, {
              type: "error", sessionId,
              error: data.error, conversationId,
            });
            break;
        }
      },
    };

    try {
      await this.pipeline!.execute(pipelineMessage, context);

      session.agentBusy = false;
      this.streamBuffers.delete(conversationId);

      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      console.log(`[gateway] Turn complete: conversation=${conversationId} elapsed=${elapsed}s`);

      this.sendToConversation(conversationId, {
        type: "done", sessionId, conversationId,
        messageId: pipelineMessage.metadata.responseMessageId || "",
        agentName: pipelineMessage.agentName || "",
        queuedCount: 0, agentStatus: "idle",
      });

      this.handleListConversations(socket).catch(() => {});
    } catch (err) {
      session.agentBusy = false;
      this.streamBuffers.delete(conversationId);
      const errMsg = err instanceof Error ? err.message : "Agent error";
      this.sendToConversation(conversationId, { type: "status", sessionId, agentStatus: "idle", conversationId });
      this.sendToConversation(conversationId, { type: "error", sessionId, error: errMsg });
    }
  }

  private async handleInterrupt(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId;
    if (!conversationId) return;

    try {
      await this.backendClient.interrupt(conversationId);
      this.send(socket, { type: "status", sessionId, agentStatus: "idle", conversationId });
    } catch (err) {
      this.send(socket, { type: "error", sessionId, error: err instanceof Error ? err.message : "Failed to interrupt" });
    }
  }

  private async handlePause(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId;
    if (!conversationId) return;

    try {
      await this.backendClient.interrupt(conversationId);
      this.send(socket, { type: "status", sessionId, agentStatus: "idle", conversationId });
    } catch (err) {
      this.send(socket, { type: "error", sessionId, error: err instanceof Error ? err.message : "Failed to pause" });
    }
  }

  /**
   * Legacy direct turn execution — fallback when pipeline is not set.
   */
  private async startTurn(
    socket: WebSocket,
    sessionId: string,
    message: string | undefined,
    conversationId: string,
    agentId?: string,
    planId?: string,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const startTime = Date.now();
    session.agentBusy = true;

    this.sessionManager.setConversationId(sessionId, conversationId);
    session.conversationId = conversationId;

    this.streamBuffers.set(conversationId, { content: "", agentName: "", agentStatus: "thinking" });

    this.sendToConversation(conversationId, {
      type: "status",
      sessionId,
      agentStatus: "thinking",
      conversationId,
    });

    try {
      let responseMessageId = "";
      let agentName = "";

      for await (const event of this.backendClient.conversationTurnStream(
        conversationId, message, agentId, planId,
      )) {
        switch (event.event) {
          case "status":
            if (!agentName && event.data.agent_name) agentName = event.data.agent_name as string;
            const agentStatus = event.data.state as "thinking" | "tool_calling" | "responding";
            const buf = this.streamBuffers.get(conversationId);
            if (buf) {
              buf.agentStatus = agentStatus;
              if (agentName) buf.agentName = agentName;
            }
            this.sendToConversation(conversationId, {
              type: "status", sessionId, agentStatus, conversationId,
            });
            break;

          case "chunk":
            const chunkContent = event.data.content as string;
            const chunkBuf = this.streamBuffers.get(conversationId);
            if (chunkBuf) chunkBuf.content += chunkContent;
            this.sendToConversation(conversationId, {
              type: "chunk", sessionId, content: chunkContent, agentName, conversationId,
            });
            break;

          case "tool_call":
            this.sendToConversation(conversationId, {
              type: "tool_call", sessionId, content: JSON.stringify(event.data), conversationId,
            });
            break;

          case "plan_created":
            this.broadcast({ type: "plan_created", sessionId,
              planId: event.data.plan_id as string, planTitle: event.data.title as string,
              planStatus: "active", conversationId });
            break;

          case "item_created":
            this.broadcast({ type: "item_updated", sessionId,
              planId: event.data.plan_id as string, itemId: event.data.item_id as string,
              itemStatus: "new", itemTitle: (event.data.title as string) || "", conversationId });
            break;

          case "item_updated":
            this.broadcast({ type: "item_updated", sessionId,
              planId: event.data.plan_id as string, itemId: event.data.item_id as string,
              itemStatus: event.data.status as string, itemTitle: (event.data.title as string) || "", conversationId });
            break;

          case "plan_completed":
            this.broadcast({ type: "plan_completed", sessionId,
              planId: event.data.plan_id as string, planStatus: event.data.status as string, conversationId });
            break;

          case "done":
            responseMessageId = (event.data.message_id as string) || "";
            if (conversationId) {
              this.sessionManager.setConversationId(sessionId, conversationId);
              session.conversationId = conversationId;
            }
            break;

          case "error":
            this.sendToConversation(conversationId, {
              type: "error", sessionId, error: event.data.message as string, conversationId,
            });
            break;
        }
      }

      session.agentBusy = false;
      this.streamBuffers.delete(conversationId);

      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      console.log(`[gateway] Turn complete: conversation=${conversationId} elapsed=${elapsed}s`);

      this.sendToConversation(conversationId, {
        type: "done", sessionId, conversationId,
        messageId: responseMessageId, agentName,
        queuedCount: 0, agentStatus: "idle",
      });

      this.handleListConversations(socket).catch(() => {});
    } catch (err) {
      session.agentBusy = false;
      this.streamBuffers.delete(conversationId);
      const errMsg = err instanceof Error ? err.message : "Agent error";
      this.sendToConversation(conversationId, { type: "status", sessionId, agentStatus: "idle", conversationId });
      this.sendToConversation(conversationId, { type: "error", sessionId, error: errMsg });
    }
  }

  private async handleSwitchConversation(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage,
  ): Promise<void> {
    if (!msg.conversationId) return;

    try {
      const conv = await this.backendClient.getConversation(msg.conversationId);
      this.sessionManager.setConversationId(sessionId, msg.conversationId);

      this.send(socket, {
        type: "history",
        sessionId,
        conversationId: msg.conversationId,
        agentId: conv.agent_id || undefined,
        agentName: conv.agent_name || undefined,
        messages: conv.messages.map((m) => ({
          role: m.role,
          content: m.content,
          id: m.id,
          created_at: m.created_at,
        })),
      });

      const buffer = this.streamBuffers.get(msg.conversationId);
      if (buffer) {
        this.send(socket, {
          type: "status",
          sessionId,
          agentStatus: buffer.agentStatus as "thinking" | "tool_calling" | "responding",
          agentName: buffer.agentName || undefined,
          conversationId: msg.conversationId,
        });
        if (buffer.content) {
          this.send(socket, {
            type: "chunk",
            sessionId,
            content: buffer.content,
            agentName: buffer.agentName || undefined,
            conversationId: msg.conversationId,
          });
        }
      }
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
    msg: IncomingMessage,
  ): Promise<void> {
    if (!msg.conversationId) return;

    try {
      await this.backendClient.deleteConversation(msg.conversationId);

      const session = this.sessionManager.getSession(sessionId);
      if (session?.conversationId === msg.conversationId) {
        session.conversationId = null;
      }

      await this.handleListConversations(socket);
    } catch (err) {
      this.send(socket, {
        type: "error",
        error: err instanceof Error ? err.message : "Failed to delete conversation",
      });
    }
  }

  private sendToConversation(conversationId: string, msg: OutgoingMessage): void {
    const payload = JSON.stringify(msg);
    const sockets = this.sessionManager.getSocketsForConversation(conversationId);
    for (const s of sockets) {
      s.send(payload);
    }
  }

  public broadcast(msg: OutgoingMessage): void {
    const payload = JSON.stringify(msg);
    for (const socket of this.sessionManager.getAllSockets()) {
      if (socket.readyState === 1) {
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
