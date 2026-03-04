/**
 * Worker client — HTTP bridge from gateway to container worker.
 *
 * Calls the worker's /turn SSE endpoint and /interrupt endpoint.
 */

import { parseSSEStream, type SSEEvent } from "./sse-parser.js";

export interface WorkerTurnRequest {
  messages: Array<{ role: string; content: string }>;
  conversation_id: string;
  plan_id?: string;
}

export interface WorkerSSEEvent extends SSEEvent {
  event: "status" | "chunk" | "tool_call" | "memory" | "done" | "error" | string;
}

const DEFAULT_TURN_TIMEOUT_MS = 1_800_000; // 30 minutes

export class WorkerClient {
  private turnTimeoutMs: number = DEFAULT_TURN_TIMEOUT_MS;

  constructor(private workerUrl: string) {}

  setTurnTimeout(ms: number): void {
    this.turnTimeoutMs = ms;
  }

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(`${this.workerUrl}/health`);
      return res.ok;
    } catch {
      return false;
    }
  }

  async *turnStream(
    req: WorkerTurnRequest,
    options?: { signal?: AbortSignal },
  ): AsyncGenerator<WorkerSSEEvent> {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.turnTimeoutMs);

    // Link external signal to our controller
    if (options?.signal) {
      if (options.signal.aborted) {
        controller.abort();
      } else {
        options.signal.addEventListener("abort", () => controller.abort(), { once: true });
      }
    }

    try {
      // Worker expects: { message, history, conversation_id }
      const lastUserMsg = req.messages.filter(m => m.role === "user").pop();
      const history = req.messages.slice(0, -1);

      const res = await fetch(`${this.workerUrl}/turn`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: lastUserMsg?.content || "",
          history,
          conversation_id: req.conversation_id,
        }),
        signal: controller.signal,
      });

      if (!res.ok) {
        const text = await res.text();
        throw new Error(`Worker error ${res.status}: ${text}`);
      }

      yield* parseSSEStream(res, { signal: controller.signal }) as AsyncGenerator<WorkerSSEEvent>;
    } finally {
      clearTimeout(timeout);
    }
  }

  async interrupt(newMessages: Array<{ role: string; content: string }>): Promise<void> {
    const res = await fetch(`${this.workerUrl}/interrupt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ new_messages: newMessages }),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Worker interrupt error ${res.status}: ${text}`);
    }
  }

}
