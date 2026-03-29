import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useAgentDiscovery } from "@/hooks/useAgentDiscovery";

// Mock fetch
const mockFetch = vi.fn();
global.fetch = mockFetch;

function createMockSSEResponse(events: Array<{ event: string; [key: string]: any }>) {
  const lines = events.map((e) => `data: ${JSON.stringify(e)}`).join("\n") + "\n";
  const encoder = new TextEncoder();
  const stream = new ReadableStream({
    start(controller) {
      controller.enqueue(encoder.encode(lines));
      controller.close();
    },
  });
  return { ok: true, body: stream };
}

describe("useAgentDiscovery", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("starts in idle status", () => {
    const { result } = renderHook(() => useAgentDiscovery());
    expect(result.current.status).toBe("idle");
    expect(result.current.activityLog).toEqual([]);
    expect(result.current.error).toBeNull();
  });

  it("transitions to connecting on startDiscovery", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: "test-session" }),
    });
    mockFetch.mockResolvedValueOnce(
      createMockSSEResponse([
        { event: "discovery_agent_started", mode: "full", session_id: "test-session" },
        {
          event: "discovery_agent_completed",
          state: {
            findings: { source: "test" },
            confidence: {},
            probes_run: [],
            user_answers: {},
            completeness: { ready: true, required_coverage: 1, recommended_coverage: 0.5, missing_required: [], low_confidence: [] },
          },
          completeness: { ready: true, required_coverage: 1, recommended_coverage: 0.5, missing_required: [], low_confidence: [] },
        },
      ])
    );

    const { result } = renderHook(() => useAgentDiscovery());

    await act(async () => {
      await result.current.startDiscovery("res-1", "dev");
    });

    expect(result.current.status).toBe("complete");
    expect(result.current.discoveryState).toBeTruthy();
    expect(result.current.completeness?.ready).toBe(true);
  });

  it("passes agentId and repoId to the API call", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: "test-session" }),
    });
    mockFetch.mockResolvedValueOnce(
      createMockSSEResponse([
        { event: "discovery_agent_started", mode: "full" },
        {
          event: "discovery_agent_completed",
          state: { findings: {}, confidence: {}, probes_run: [], user_answers: {}, completeness: { ready: true, required_coverage: 1, recommended_coverage: 0, missing_required: [], low_confidence: [] } },
          completeness: { ready: true, required_coverage: 1, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        },
      ])
    );

    const { result } = renderHook(() => useAgentDiscovery());

    await act(async () => {
      await result.current.startDiscovery("", "dev", undefined, "agent-123", "repo-456");
    });

    // Verify the POST body contains agent_id and repo_id
    const postCall = mockFetch.mock.calls[0];
    const body = JSON.parse(postCall[1].body);
    expect(body.agent_id).toBe("agent-123");
    expect(body.repo_id).toBe("repo-456");
  });

  it("sets error when no session_id returned", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ status: "ok" }), // no session_id
    });

    const { result } = renderHook(() => useAgentDiscovery());

    await act(async () => {
      await result.current.startDiscovery("res-1", "dev");
    });

    expect(result.current.status).toBe("error");
    expect(result.current.error).toBe("Agent discovery did not return a session");
  });

  it("handles question events", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: "test-session" }),
    });

    // Create a stream that stays open after sending the question event
    let resolveStream: () => void;
    const streamDone = new Promise<void>((r) => { resolveStream = r; });
    const encoder = new TextEncoder();
    const stream = new ReadableStream({
      start(controller) {
        const lines = [
          `data: ${JSON.stringify({ event: "discovery_agent_started", mode: "full" })}`,
          `data: ${JSON.stringify({ event: "discovery_user_question", question: { question: "What port?", context: "Could not detect", field: "app_port", options: ["3000", "8080"], default: "3000", questions_remaining: 1 } })}`,
        ].join("\n") + "\n";
        controller.enqueue(encoder.encode(lines));
        // Keep the stream open — close when test is done
        streamDone.then(() => controller.close());
      },
    });
    mockFetch.mockResolvedValueOnce({ ok: true, body: stream });

    const { result } = renderHook(() => useAgentDiscovery());

    // Start discovery but don't await (stream stays open)
    act(() => {
      result.current.startDiscovery("res-1", "dev");
    });

    // Wait for events to be processed
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });

    expect(result.current.currentQuestion?.field).toBe("app_port");
    expect(result.current.questionsRemaining).toBe(1);

    // Cleanup
    resolveStream!();
  });

  it("editField updates discovery state", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ session_id: "s1" }),
    });
    mockFetch.mockResolvedValueOnce(
      createMockSSEResponse([
        { event: "discovery_agent_started", mode: "full" },
        {
          event: "discovery_agent_completed",
          state: {
            findings: { source: "test", app_port: 3000 },
            confidence: { app_port: { source: "detected", detail: "test", score: 0.8 } },
            probes_run: [],
            user_answers: {},
            completeness: { ready: true, required_coverage: 1, recommended_coverage: 0, missing_required: [], low_confidence: [] },
          },
          completeness: { ready: true, required_coverage: 1, recommended_coverage: 0, missing_required: [], low_confidence: [] },
        },
      ])
    );

    const { result } = renderHook(() => useAgentDiscovery());

    await act(async () => {
      await result.current.startDiscovery("res-1", "dev");
    });

    act(() => {
      result.current.editField("app_port", "8080");
    });

    expect(result.current.discoveryState?.findings.app_port).toBe("8080");
    expect(result.current.discoveryState?.confidence.app_port.source).toBe("user-provided");
  });
});
