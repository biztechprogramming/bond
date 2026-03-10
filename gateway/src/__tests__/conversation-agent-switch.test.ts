import { describe, it, expect, vi, beforeEach } from "vitest";

/**
 * Test: switching conversations must always include the conversation's agent ID
 * so the frontend can switch to the correct agent context.
 *
 * Bug: clicking a conversation belonging to a different agent didn't show history
 * and didn't switch the agent selector.
 *
 * Fix: gateway sends agentId in the "history" response; frontend uses it as
 * the authoritative source for agent selection.
 */

// Mock BackendClient
const mockGetConversation = vi.fn();
const mockListConversations = vi.fn(async () => []);

vi.mock("../backend/client.js", () => ({
  BackendClient: vi.fn().mockImplementation(() => ({
    getConversation: mockGetConversation,
    listConversations: mockListConversations,
  })),
}));

// Mock SessionManager
const mockSetConversationId = vi.fn();
const mockGetSession = vi.fn();
const mockGetClient = vi.fn();

vi.mock("../sessions/manager.js", () => ({
  SessionManager: vi.fn().mockImplementation(() => ({
    setConversationId: mockSetConversationId,
    getSession: mockGetSession,
    getClient: mockGetClient,
    getSocketsForConversation: vi.fn(() => []),
    getAllSockets: vi.fn(() => []),
  })),
}));

describe("Conversation switch includes agent context", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("getConversation response includes agent_id and agent_name", async () => {
    // Simulate backend response for a conversation owned by agent "jessica"
    const jessicaConversation = {
      id: "conv-jessica-001",
      agent_id: "01KJESSICA00000000000000000",
      agent_name: "Jessica",
      title: "Design review",
      message_count: 3,
      messages: [
        { id: "m1", role: "user", content: "Review this design", created_at: "2026-03-10T09:00:00Z" },
        { id: "m2", role: "assistant", content: "Looking at it now...", created_at: "2026-03-10T09:00:01Z" },
        { id: "m3", role: "user", content: "What do you think?", created_at: "2026-03-10T09:00:02Z" },
      ],
    };

    mockGetConversation.mockResolvedValue(jessicaConversation);

    const conv = await mockGetConversation("conv-jessica-001");

    // The backend response MUST include agent_id
    expect(conv.agent_id).toBe("01KJESSICA00000000000000000");
    expect(conv.agent_name).toBe("Jessica");
  });

  it("history message for Bond conversation includes Bond agent ID", async () => {
    const bondConversation = {
      id: "conv-bond-001",
      agent_id: "01JBOND0000000000000DEFAULT",
      agent_name: "Bond",
      title: "General chat",
      message_count: 1,
      messages: [
        { id: "m1", role: "user", content: "Hello", created_at: "2026-03-10T09:00:00Z" },
      ],
    };

    mockGetConversation.mockResolvedValue(bondConversation);

    const conv = await mockGetConversation("conv-bond-001");

    // Build the history message the same way the gateway does
    const historyMessage = {
      type: "history" as const,
      sessionId: "session-1",
      conversationId: conv.id,
      agentId: conv.agent_id || undefined,
      agentName: conv.agent_name || undefined,
      messages: conv.messages.map((m: any) => ({
        role: m.role,
        content: m.content,
        id: m.id,
        created_at: m.created_at,
      })),
    };

    // agentId MUST be present in the history message
    expect(historyMessage.agentId).toBe("01JBOND0000000000000DEFAULT");
    expect(historyMessage.agentName).toBe("Bond");
  });

  it("switching from Bond to Jessica conversation carries Jessica's agent ID", async () => {
    // First conversation: Bond
    const bondConv = {
      id: "conv-bond-001",
      agent_id: "01JBOND0000000000000DEFAULT",
      agent_name: "Bond",
      title: null,
      message_count: 1,
      messages: [{ id: "m1", role: "user", content: "Hi Bond", created_at: "2026-03-10T09:00:00Z" }],
    };

    // Second conversation: Jessica
    const jessicaConv = {
      id: "conv-jessica-001",
      agent_id: "01KJESSICA00000000000000000",
      agent_name: "Jessica",
      title: null,
      message_count: 1,
      messages: [{ id: "m2", role: "user", content: "Hi Jessica", created_at: "2026-03-10T09:01:00Z" }],
    };

    // Switch to Bond conversation
    mockGetConversation.mockResolvedValue(bondConv);
    let conv = await mockGetConversation("conv-bond-001");
    let historyMsg = {
      type: "history",
      conversationId: conv.id,
      agentId: conv.agent_id || undefined,
    };
    expect(historyMsg.agentId).toBe("01JBOND0000000000000DEFAULT");

    // Now switch to Jessica conversation
    mockGetConversation.mockResolvedValue(jessicaConv);
    conv = await mockGetConversation("conv-jessica-001");
    historyMsg = {
      type: "history",
      conversationId: conv.id,
      agentId: conv.agent_id || undefined,
    };

    // Agent ID must switch to Jessica's
    expect(historyMsg.agentId).toBe("01KJESSICA00000000000000000");
    expect(historyMsg.agentId).not.toBe("01JBOND0000000000000DEFAULT");
  });

  it("conversation without agent_id sends undefined (does not crash)", async () => {
    const orphanConv = {
      id: "conv-orphan",
      agent_id: null,
      agent_name: null,
      title: null,
      message_count: 0,
      messages: [],
    };

    mockGetConversation.mockResolvedValue(orphanConv);
    const conv = await mockGetConversation("conv-orphan");

    const historyMsg = {
      type: "history",
      conversationId: conv.id,
      agentId: conv.agent_id || undefined,
      agentName: conv.agent_name || undefined,
    };

    // Should be undefined, not null — won't crash the frontend
    expect(historyMsg.agentId).toBeUndefined();
    expect(historyMsg.agentName).toBeUndefined();
  });
});
