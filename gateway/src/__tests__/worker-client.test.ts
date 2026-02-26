import { describe, it, expect, vi, beforeEach } from "vitest";
import { WorkerClient } from "../backend/worker-client.js";

function makeSSEResponse(body: string, status = 200): Response {
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(body));
      controller.close();
    },
  });
  return new Response(stream, { status });
}

describe("WorkerClient", () => {
  let client: WorkerClient;

  beforeEach(() => {
    client = new WorkerClient("http://localhost:18793");
    vi.restoreAllMocks();
  });

  it("health check success", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), { status: 200 }),
    );
    expect(await client.healthCheck()).toBe(true);
  });

  it("health check failure", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));
    expect(await client.healthCheck()).toBe(false);
  });

  it("turn stream parses all event types", async () => {
    const body =
      'event: status\ndata: {"state":"thinking"}\n\n' +
      'event: chunk\ndata: {"content":"hello"}\n\n' +
      'event: done\ndata: {"response":"hello","tool_calls_made":1}\n\n';

    vi.spyOn(globalThis, "fetch").mockResolvedValue(makeSSEResponse(body));

    const events = [];
    for await (const event of client.turnStream({
      messages: [{ role: "user", content: "hi" }],
      conversation_id: "conv-1",
    })) {
      events.push(event);
    }

    expect(events).toHaveLength(3);
    expect(events[0].event).toBe("status");
    expect(events[1].event).toBe("chunk");
    expect(events[2].event).toBe("done");
  });

  it("turn stream connection error", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));

    const gen = client.turnStream({
      messages: [{ role: "user", content: "hi" }],
      conversation_id: "conv-1",
    });

    await expect(async () => {
      for await (const _event of gen) {
        // should throw
      }
    }).rejects.toThrow("ECONNREFUSED");
  });

  it("interrupt sends correct payload", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ acknowledged: true }), { status: 200 }),
    );

    await client.interrupt([{ role: "user", content: "stop" }]);

    expect(fetchSpy).toHaveBeenCalledWith(
      "http://localhost:18793/interrupt",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ new_messages: [{ role: "user", content: "stop" }] }),
      }),
    );
  });
});
