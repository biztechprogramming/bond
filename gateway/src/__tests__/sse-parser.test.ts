import { describe, it, expect } from "vitest";
import { parseSSEStream } from "../backend/sse-parser.js";

function makeResponse(body: string): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream);
}

function makeChunkedResponse(chunks: string[]): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(stream);
}

async function collectEvents(gen: AsyncGenerator<any>) {
  const events = [];
  for await (const event of gen) {
    events.push(event);
  }
  return events;
}

describe("parseSSEStream", () => {
  it("parses single event", async () => {
    const res = makeResponse('event: status\ndata: {"state":"thinking"}\n\n');
    const events = await collectEvents(parseSSEStream(res));
    expect(events).toHaveLength(1);
    expect(events[0]).toEqual({ event: "status", data: { state: "thinking" } });
  });

  it("parses multiple events", async () => {
    const body =
      'event: status\ndata: {"state":"thinking"}\n\n' +
      'event: chunk\ndata: {"content":"hello"}\n\n' +
      'event: done\ndata: {"response":"hello"}\n\n';
    const res = makeResponse(body);
    const events = await collectEvents(parseSSEStream(res));
    expect(events).toHaveLength(3);
    expect(events[0].event).toBe("status");
    expect(events[1].event).toBe("chunk");
    expect(events[2].event).toBe("done");
  });

  it("handles partial chunks", async () => {
    const res = makeChunkedResponse([
      'event: status\ndata: {"sta',
      'te":"thinking"}\n\nevent: done\ndata: {"ok":true}\n\n',
    ]);
    const events = await collectEvents(parseSSEStream(res));
    expect(events).toHaveLength(2);
    expect(events[0]).toEqual({ event: "status", data: { state: "thinking" } });
    expect(events[1]).toEqual({ event: "done", data: { ok: true } });
  });

  it("handles unnamed events (defaults to message)", async () => {
    const res = makeResponse('data: {"content":"test"}\n\n');
    const events = await collectEvents(parseSSEStream(res));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("message");
  });

  it("handles empty lines between events", async () => {
    const body =
      'event: a\ndata: {"v":1}\n\n\n\nevent: b\ndata: {"v":2}\n\n';
    const res = makeResponse(body);
    const events = await collectEvents(parseSSEStream(res));
    expect(events).toHaveLength(2);
  });

  it("skips malformed JSON data", async () => {
    const body =
      'event: bad\ndata: not-json\n\n' +
      'event: good\ndata: {"ok":true}\n\n';
    const res = makeResponse(body);
    const events = await collectEvents(parseSSEStream(res));
    expect(events).toHaveLength(1);
    expect(events[0].event).toBe("good");
  });

  it("handles multi-line data", async () => {
    // Multi-line data where the full JSON is on one data line
    const body = 'event: test\ndata: {"multi":"line","test":true}\n\n';
    const res = makeResponse(body);
    const events = await collectEvents(parseSSEStream(res));
    expect(events).toHaveLength(1);
    expect(events[0].data).toEqual({ multi: "line", test: true });
  });

  it("aborts on signal", async () => {
    const controller = new AbortController();
    controller.abort();
    const res = makeResponse('event: a\ndata: {"v":1}\n\n');
    const events = await collectEvents(parseSSEStream(res, { signal: controller.signal }));
    expect(events).toHaveLength(0);
  });
});
