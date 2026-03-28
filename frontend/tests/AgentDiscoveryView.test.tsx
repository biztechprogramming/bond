import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import React from "react";

import type { UseAgentDiscoveryReturn } from "@/hooks/useAgentDiscovery";

// Mock the hook before importing the component
const mockHook: UseAgentDiscoveryReturn = {
  status: "idle",
  discoveryMode: "full",
  activityLog: [],
  currentQuestion: null,
  questionsRemaining: 0,
  discoveryState: null,
  completeness: null,
  probesRun: [],
  error: null,
  startDiscovery: vi.fn(),
  answerQuestion: vi.fn(),
  cancelDiscovery: vi.fn(),
  editField: vi.fn(),
  forceComplete: vi.fn(),
};

vi.mock("@/hooks/useAgentDiscovery", () => ({
  useAgentDiscovery: () => mockHook,
}));

import AgentDiscoveryView from "@/components/discovery/AgentDiscoveryView";

describe("AgentDiscoveryView", () => {
  const defaultProps = {
    resourceId: "res-1",
    environment: "dev",
    onComplete: vi.fn(),
    onFallback: vi.fn(),
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

  it("returns null when idle", () => {
    const { container } = render(<AgentDiscoveryView {...defaultProps} />);
    expect(container.innerHTML).toBe("");
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

  it("shows activity log items", () => {
    mockHook.status = "discovering";
    mockHook.activityLog = [
      { id: "1", type: "discovery", message: "Discovered framework", timestamp: Date.now(), status: "done" },
      { id: "2", type: "discovery", message: "Discovered ports", timestamp: Date.now(), status: "done" },
    ];
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText("Discovered framework")).toBeTruthy();
    expect(screen.getByText("Discovered ports")).toBeTruthy();
  });

  it("calls onFallback when error is no_session", () => {
    mockHook.error = "no_session";
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(defaultProps.onFallback).toHaveBeenCalled();
  });

  it("shows complete state", () => {
    mockHook.status = "complete";
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText("Discovery Complete")).toBeTruthy();
  });
});
