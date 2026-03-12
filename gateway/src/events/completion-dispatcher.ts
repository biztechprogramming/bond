/**
 * CompletionDispatcher — triggers an agent turn when an event matches a subscription.
 * See design doc 040-gateway-event-subscriptions.md §3.2.5
 */

import type { BackendClient } from "../backend/client.js";
import type { GatewayEvent, EventSubscription } from "./types.js";

// Rate limiting: max 3 auto-turns per conversation per 60s
const RATE_LIMIT_MAX = 3;
const RATE_LIMIT_WINDOW_MS = 60_000;

export class CompletionDispatcher {
  // conversationId -> array of timestamps (unix ms) for recent dispatches
  private rateLimitMap = new Map<string, number[]>();

  constructor(
    private backendClient: BackendClient,
    private sendToConversation: (conversationId: string, msg: unknown) => void,
  ) {}

  async dispatch(event: GatewayEvent, subscription: EventSubscription): Promise<void> {
    const { conversationId, agentId } = subscription;

    // 1. Rate limit check
    if (this.isRateLimited(conversationId)) {
      console.warn(`[events] Rate limited: ${conversationId}`);
      return;
    }
    this.recordDispatch(conversationId);

    // 2. Build system message
    const systemMessage = this.buildSystemMessage(event, subscription);

    // 3. Notify frontend that an event arrived
    this.sendToConversation(conversationId, {
      type: "status",
      sessionId: "system",
      agentStatus: "thinking",
      conversationId,
    });

    try {
      // 4. Trigger agent turn via the backend and stream response
      for await (const sseEvent of this.backendClient.conversationTurnStream(
        conversationId,
        systemMessage,
        agentId || undefined,
      )) {
        switch (sseEvent.event) {
          case "chunk":
            this.sendToConversation(conversationId, {
              type: "chunk",
              sessionId: "system",
              content: sseEvent.data.content as string,
              conversationId,
            });
            break;
          case "status":
            this.sendToConversation(conversationId, {
              type: "status",
              sessionId: "system",
              agentStatus: sseEvent.data.state,
              conversationId,
            });
            break;
          case "done":
            this.sendToConversation(conversationId, {
              type: "done",
              sessionId: "system",
              conversationId,
              messageId: sseEvent.data.message_id || "",
              agentName: "",
              queuedCount: 0,
              agentStatus: "idle",
            });
            break;
          case "error":
            this.sendToConversation(conversationId, {
              type: "error",
              sessionId: "system",
              error: sseEvent.data.message,
              conversationId,
            });
            break;
        }
      }
    } catch (err) {
      const errMsg = err instanceof Error ? err.message : "Event dispatch error";
      console.error(`[events] Dispatch error for ${conversationId}:`, err);
      this.sendToConversation(conversationId, {
        type: "error",
        sessionId: "system",
        error: errMsg,
        conversationId,
      });
    }
  }

  private isRateLimited(conversationId: string): boolean {
    const now = Date.now();
    const timestamps = this.rateLimitMap.get(conversationId) ?? [];
    const recent = timestamps.filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
    return recent.length >= RATE_LIMIT_MAX;
  }

  private recordDispatch(conversationId: string): void {
    const now = Date.now();
    const timestamps = this.rateLimitMap.get(conversationId) ?? [];
    const recent = timestamps.filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
    recent.push(now);
    this.rateLimitMap.set(conversationId, recent);
  }

  private buildSystemMessage(event: GatewayEvent, sub: EventSubscription): string {
    return [
      `[SYSTEM EVENT: ${event.type} on ${event.repo}]`,
      `Branch: ${event.branch || "N/A"}`,
      `Actor: ${event.actor || "unknown"}`,
      `Context: ${sub.context}`,
      ``,
      `You previously spawned a coding agent and subscribed to be notified when it pushed.`,
      `The push has landed. Summarize the changes for the user and report the status.`,
      ``,
      `IMPORTANT: Do NOT spawn another coding agent in this response.`,
      `If there are follow-up actions needed, describe them but let the user decide.`,
    ].join("\n");
  }
}
