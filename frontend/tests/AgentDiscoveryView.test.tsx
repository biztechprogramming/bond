import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

import type { UseAgentDiscoveryReturn } from "@/hooks/useAgentDiscovery";

// Mock the hook before importing the component
const mockHook: UseAgentDiscoveryReturn = {
  status: "idle",
  discoveryMode: "full",
  activityLog: [],
  conversationMessages: [],
  rawEvents: [],
  currentQuestion: null,
  questionsRemaining: 0,
  discoveryState: null,
  completeness: null,
  probesRun: [],
  error: null,
  startDiscovery: vi.fn().mockResolvedValue(undefined),
  answerQuestion: vi.fn(),
  cancelDiscovery: vi.fn(),
  editField: vi.fn(),
  forceComplete: vi.fn(),
};

vi.mock("@/hooks/useAgentDiscovery", () => ({
  useAgentDiscovery: () => mockHook,
}));

vi.mock("@/hooks/useSpacetimeDB", () => ({
  useAgents: () => [],
  useAgentMounts: () => [],
}));

import AgentDiscoveryView from "@/components/discovery/AgentDiscoveryView";

describe("AgentDiscoveryView", () => {
  const defaultProps = {
    agentId: "agent-1",
    repoId: "my-app",
    environment: "dev",
    onComplete: vi.fn(),
    onCancel: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    Object.assign(mockHook, {
      status: "idle",
      discoveryMode: "full",
      activityLog: [],
      currentQuestion: null,
      error: null,
      discoveryState: null,
      completeness: null,
    });
  });

  // --- Error when no agentId ---

  it("shows error when agentId is empty string", () => {
    render(<AgentDiscoveryView {...defaultProps} agentId="" />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/No agent selected/)).toBeTruthy();
  });

  it("shows error when agentId is undefined", () => {
    render(<AgentDiscoveryView {...defaultProps} agentId={undefined as any} />);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/No agent selected/)).toBeTruthy();
  });

  // --- Agent-first mode tests (agentId provided via props) ---

  it("starts discovery when agentId is provided", () => {
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(mockHook.startDiscovery).toHaveBeenCalledWith("", "dev", undefined, "agent-1", "my-app");
  });

  it("shows discovering state", () => {
    mockHook.status = "discovering";
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText("Discovering your stack...")).toBeTruthy();
  });

  it("shows cancel button during discovery", () => {
    mockHook.status = "discovering";
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText("Cancel")).toBeTruthy();
  });

  it("shows error state", () => {
    mockHook.status = "error";
    mockHook.error = "Connection refused";
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText(/Connection refused/)).toBeTruthy();
  });

  it("shows conversation messages", () => {
    mockHook.status = "discovering";
    (mockHook as any).conversationMessages = [
      { id: "c1", type: "assistant", content: "Analyzing your codebase...", timestamp: Date.now() },
    ];
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText("Analyzing your codebase...")).toBeTruthy();
  });

  it("shows complete state", () => {
    mockHook.status = "complete";
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText("Discovery Complete")).toBeTruthy();
  });
});
