/**
 * Completion Turn Handler — triggers agent turns when background tasks finish.
 *
 * When the SpacetimeDB subscription detects a new system_events row
 * (e.g., coding agent completed), this handler:
 *
 * 1. Builds a system-context message describing the completion
 * 2. Triggers a new agent turn via the backend
 * 3. Streams the response to WebSocket clients
 * 4. Consumes the system event (deletes the row)
 *
 * Guard rails prevent runaway loops: rate limiting, no recursive spawns.
 */

import type { BackendClient, SSEEvent } from "../backend/client.js";
import type { SystemEventRow } from "../spacetimedb/subscription.js";
import { callReducer } from "../spacetimedb/client.js";
import type { GatewayConfig } from "../config/index.js";

export type BroadcastToConversationFn = (conversationId: string, message: Record<string, unknown>) => void;

/** Rate limit: max auto-turns per conversation per window. */
const MAX_AUTO_TURNS_PER_MINUTE = 3;
const RATE_LIMIT_WINDOW_MS = 60_000;

export class CompletionHandler {
  private rateLimits = new Map<string, { count: number; resetAt: number }>();
  private processing = new Set<string>(); // Prevent concurrent handling of same event

  constructor(
    private config: GatewayConfig,
    private backendClient: BackendClient,
    private broadcastToConversation: BroadcastToConversationFn,
  ) {}

  /**
   * Handle a system event from SpacetimeDB subscription.
   * Triggers an agent turn and streams the response to the frontend.
   */
  async handleEvent(event: SystemEventRow): Promise<void> {
    // Deduplicate — don't process same event twice
    if (this.processing.has(event.id)) return;
    this.processing.add(event.id);

    try {
      // Rate limit check
      if (!this.checkRateLimit(event.conversationId)) {
        console.warn(
          `[completion] Rate limited for conversation ${event.conversationId} — skipping ${event.eventType}`,
        );
        await this.consumeEvent(event.id);
        return;
      }

      console.log(
        `[completion] Handling ${event.eventType} for conversation ${event.conversationId}`,
      );

      // Build the completion message for the agent
      const systemMessage = this.buildCompletionMessage(event);

      // Notify frontend that a completion-triggered turn is starting
      this.broadcastToConversation(event.conversationId, {
        type: "status",
        agentStatus: "thinking",
        conversationId: event.conversationId,
        isCompletionTurn: true,
      });

      // Trigger agent turn with completion context
      let fullResponse = "";
      let toolCallsMade = 0;

      try {
        for await (const sse of this.backendClient.conversationTurnStream(
          event.conversationId,
          systemMessage,
          event.agentId || undefined,
        )) {
          const parsed = this.parseSSE(sse);
          if (!parsed) continue;

          if (parsed.type === "chunk" && parsed.content) {
            fullResponse += parsed.content;
            this.broadcastToConversation(event.conversationId, {
              type: "stream_chunk",
              content: parsed.content,
              conversationId: event.conversationId,
              isCompletionTurn: true,
            });
          } else if (parsed.type === "done") {
            toolCallsMade = (parsed.tool_calls_made as number) ?? 0;
          }
        }
      } catch (err) {
        console.error("[completion] Agent turn failed:", err);
        this.broadcastToConversation(event.conversationId, {
          type: "error",
          error: "Completion turn failed — the agent could not summarize the background task results.",
          conversationId: event.conversationId,
        });
      }

      // Notify frontend the turn is done
      this.broadcastToConversation(event.conversationId, {
        type: "status",
        agentStatus: "idle",
        conversationId: event.conversationId,
        isCompletionTurn: true,
      });

      if (fullResponse) {
        // Send the final complete message (frontend may need this for persistence)
        this.broadcastToConversation(event.conversationId, {
          type: "response",
          response: fullResponse,
          conversationId: event.conversationId,
          isCompletionTurn: true,
          toolCallsMade,
        });
      }
    } finally {
      // Always consume the event and clear processing flag
      await this.consumeEvent(event.id);
      this.processing.delete(event.id);
    }
  }

  /**
   * Build the system message that will be injected as the "user" message
   * for the completion turn. Instructs the LLM to summarize and suggest next steps.
   */
  buildCompletionMessage(event: SystemEventRow): string {
    let metadata: Record<string, unknown> = {};
    try {
      metadata = JSON.parse(event.metadata);
    } catch {
      /* empty metadata is fine */
    }

    if (event.eventType === "coding_agent_done") {
      const parts = [
        "[System: Background coding agent completed successfully]",
        "",
        event.summary,
      ];
      if (metadata.git_stat) {
        parts.push("", `Files changed:\n\`\`\`\n${metadata.git_stat}\n\`\`\``);
      }
      parts.push(
        "",
        "Summarize the results for the user. Describe what was built or changed and suggest next steps (e.g., run tests, review the diff, create a PR).",
        "If there is more work to do, you may spawn additional coding agents.",
      );
      return parts.filter((p) => p !== undefined).join("\n");
    }

    if (event.eventType === "coding_agent_failed") {
      const parts = [
        "[System: Background coding agent failed]",
        "",
        event.summary,
        "",
        `Exit code: ${metadata.exit_code ?? "unknown"}`,
      ];
      if (metadata.error) {
        parts.push(`Error: ${metadata.error}`);
      }
      parts.push(
        "",
        "Explain what went wrong to the user and suggest how to fix it or retry.",
        "You may spawn another coding agent to retry if appropriate.",
      );
      return parts.filter((p) => p !== undefined).join("\n");
    }

    // Generic system event
    return `[System: ${event.eventType}]\n\n${event.summary}`;
  }

  /**
   * Consume (delete) a system event after processing.
   */
  private async consumeEvent(eventId: string): Promise<void> {
    try {
      await callReducer(
        this.config.spacetimedbUrl,
        this.config.spacetimedbModuleName,
        "consume_system_event",
        [eventId],
        this.config.spacetimedbToken,
      );
    } catch (err) {
      console.error("[completion] Failed to consume event:", err);
    }
  }

  /**
   * Rate limit: max N auto-turns per conversation per minute.
   */
  private checkRateLimit(conversationId: string): boolean {
    const now = Date.now();
    const limit = this.rateLimits.get(conversationId);

    if (!limit || now > limit.resetAt) {
      this.rateLimits.set(conversationId, {
        count: 1,
        resetAt: now + RATE_LIMIT_WINDOW_MS,
      });
      return true;
    }

    if (limit.count >= MAX_AUTO_TURNS_PER_MINUTE) {
      return false;
    }

    limit.count++;
    return true;
  }

  /**
   * Parse an SSE event from the backend stream.
   */
  private parseSSE(sse: SSEEvent): Record<string, unknown> | null {
    if (!sse.data) return null;
    return { type: sse.event, ...sse.data };
  }
}
