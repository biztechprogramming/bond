/**
 * Shared SSE stream parser — used by both BackendClient and WorkerClient.
 *
 * Parses Server-Sent Events from a fetch Response into typed event objects.
 */

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface SSEParserOptions {
  signal?: AbortSignal;
}

/**
 * Parse an SSE stream from a fetch Response into an async generator of events.
 *
 * Handles:
 * - Partial chunks (data split across reads)
 * - Multi-line data fields
 * - Unnamed events (defaults to "message")
 * - AbortSignal for cancellation
 * - Cleanup on generator return/throw
 */
export async function* parseSSEStream(
  response: Response,
  options?: SSEParserOptions,
): AsyncGenerator<SSEEvent> {
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  // Persist across chunks
  let currentEvent = "";
  let dataLines: string[] = [];

  try {
    while (true) {
      if (options?.signal?.aborted) {
        break;
      }

      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          dataLines.push(line.slice(6));
        } else if (line === "") {
          // Empty line = end of event
          if (dataLines.length > 0) {
            const dataStr = dataLines.join("\n");
            try {
              const data = JSON.parse(dataStr) as Record<string, unknown>;
              yield { event: currentEvent || "message", data };
            } catch {
              // Skip malformed JSON data
            }
          }
          currentEvent = "";
          dataLines = [];
        }
      }
    }

    // Handle any remaining data after stream ends
    if (dataLines.length > 0) {
      const dataStr = dataLines.join("\n");
      try {
        const data = JSON.parse(dataStr) as Record<string, unknown>;
        yield { event: currentEvent || "message", data };
      } catch {
        // Skip incomplete data
      }
    }
  } finally {
    reader.releaseLock();
  }
}
