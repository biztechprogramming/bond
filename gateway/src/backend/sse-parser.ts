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
  console.log(`[SSE-PARSER] Starting to parse SSE stream, has body: ${!!response.body}`);
  const reader = response.body?.getReader();
  if (!reader) throw new Error("No response body");

  const decoder = new TextDecoder();
  let buffer = "";
  // Persist across chunks
  let currentEvent = "";
  let dataLines: string[] = [];

  try {
    let chunkCount = 0;
    while (true) {
      if (options?.signal?.aborted) {
        console.log(`[SSE-PARSER] Signal aborted`);
        break;
      }

      const { done, value } = await reader.read();
      chunkCount++;
      console.log(`[SSE-PARSER] Read chunk ${chunkCount}: done=${done}, hasValue=${!!value}, valueLength=${value?.length || 0}`);
      if (done) {
        console.log(`[SSE-PARSER] Stream done after ${chunkCount} chunks`);
        break;
      }

      const decoded = decoder.decode(value, { stream: true });
      // Show actual character codes for first few chars
      const firstChars = decoded.substring(0, 20);
      const charCodes = Array.from(firstChars).map(c => c.charCodeAt(0)).join(',');
      console.log(`[SSE-PARSER] Decoded chunk (${decoded.length} chars), first 20 char codes: ${charCodes}`);
      console.log(`[SSE-PARSER] Decoded chunk preview: ${decoded.substring(0, 50).replace(/\n/g, '\\n').replace(/\r/g, '\\r')}`);
      buffer += decoded;
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";
      console.log(`[SSE-PARSER] Split into ${lines.length} lines, buffer remaining: ${buffer.length} chars`);
      if (lines.length > 0) {
        console.log(`[SSE-PARSER] First line: ${lines[0].substring(0, 50)}`);
      }

      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
          console.log(`[SSE-PARSER] Parsed event: ${currentEvent}`);
        } else if (line.startsWith("data: ")) {
          const dataLine = line.slice(6);
          dataLines.push(dataLine);
          console.log(`[SSE-PARSER] Parsed data line (${dataLine.length} chars): ${dataLine.substring(0, 100)}`);
        } else if (line === "") {
          // Empty line = end of event
          if (dataLines.length > 0) {
            const dataStr = dataLines.join("\n");
            try {
              const data = JSON.parse(dataStr) as Record<string, unknown>;
              console.log(`[SSE-PARSER] Yielding event: ${currentEvent || "message"} with data keys: ${Object.keys(data)}`);
              yield { event: currentEvent || "message", data };
            } catch (e) {
              console.error(`[SSE-PARSER] Failed to parse JSON: ${e}, data: ${dataStr.substring(0, 200)}`);
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
