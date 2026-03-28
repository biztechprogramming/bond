import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";

import type { UseAgentDiscoveryReturn } from "@/hooks/useAgentDiscovery";

// Mock the hook before importing the component
const mockHook: UseAgentDiscoveryReturn = {
  status: "idle",
  discoveryMode: "full",
  activityLog: [],
  rawEvents: [],
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

const mockAgents = [
  { id: "agent-1", name: "deploy-dev", displayName: "Dev Agent", systemPrompt: "", model: "gpt-4", utilityModel: "gpt-4", tools: "", sandboxImage: "", maxIterations: 10, isActive: true, isDefault: true },
  { id: "agent-2", name: "deploy-prod", displayName: "Prod Agent", systemPrompt: "", model: "gpt-4", utilityModel: "gpt-4", tools: "", sandboxImage: "", maxIterations: 10, isActive: true, isDefault: false },
];

const mockMounts = [
  { id: "mount-1", agentId: "agent-1", hostPath: "/repos/my-app", mountName: "my-app", containerPath: "/workspace/my-app", readonly: false },
];

vi.mock("@/hooks/useSpacetimeDB", () => ({
  useAgents: () => mockAgents,
  useAgentMounts: (agentId: string) => agentId === "agent-1" ? mockMounts : [],
}));

import AgentDiscoveryView from "@/components/discovery/AgentDiscoveryView";

describe("AgentDiscoveryView", () => {
  const defaultProps = {
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

  // --- Agent/repo selection pre-step tests ---

  it("shows agent selector when no resourceId is provided", () => {
    render(<AgentDiscoveryView {...defaultProps} />);
    expect(screen.getByText("Start Deployment Discovery")).toBeTruthy();
    expect(screen.getByText("Select an agent...")).toBeTruthy();
  });

  it("shows repo selector after selecting an agent with mounts", () => {
    render(<AgentDiscoveryView {...defaultProps} />);
    const agentSelect = screen.getAllByRole("combobox")[0];
    fireEvent.change(agentSelect, { target: { value: "agent-1" } });
    expect(screen.getByText("Select a repository...")).toBeTruthy();
  });

  it("shows hint when agent has no mounts", () => {
    render(<AgentDiscoveryView {...defaultProps} />);
    const agentSelect = screen.getAllByRole("combobox")[0];
    fireEvent.change(agentSelect, { target: { value: "agent-2" } });
    expect(screen.getByText(/No repos mounted/)).toBeTruthy();
  });

  it("calls startDiscovery with agentId and repoId on Start", () => {
    render(<AgentDiscoveryView {...defaultProps} />);
    const agentSelect = screen.getAllByRole("combobox")[0];
    fireEvent.change(agentSelect, { target: { value: "agent-1" } });
    const repoSelect = screen.getAllByRole("combobox")[1];
    fireEvent.change(repoSelect, { target: { value: "my-app" } });
    fireEvent.click(screen.getByText("Start Discovery"));
    expect(mockHook.startDiscovery).toHaveBeenCalledWith("", "dev", undefined, "agent-1", "my-app");
  });

  // --- Agent-first mode tests (agentId provided via props) ---

  it("skips selector and starts discovery when agentId is provided", () => {
    render(<AgentDiscoveryView {...defaultProps} agentId="agent-1" repoId="my-app" />);
    expect(mockHook.startDiscovery).toHaveBeenCalledWith("", "dev", undefined, "agent-1", "my-app");
  });

  // --- Legacy mode tests (resourceId provided) ---

  it("skips selector and starts discovery when resourceId is provided", () => {
    render(<AgentDiscoveryView {...defaultProps} resourceId="res-1" />);
    expect(mockHook.startDiscovery).toHaveBeenCalledWith("res-1", "dev", undefined);
  });

  it("shows discovering state", () => {
    mockHook.status = "discovering";
    render(<AgentDiscoveryView {...defaultProps} resourceId="res-1" />);
    expect(screen.getByText("Discovering your stack...")).toBeTruthy();
  });

  it("shows cancel button during discovery", () => {
    mockHook.status = "discovering";
    render(<AgentDiscoveryView {...defaultProps} resourceId="res-1" />);
    expect(screen.getByText("Cancel")).toBeTruthy();
  });

  it("shows error state", () => {
    mockHook.status = "error";
    mockHook.error = "Connection refused";
    render(<AgentDiscoveryView {...defaultProps} resourceId="res-1" />);
    expect(screen.getByText(/Connection refused/)).toBeTruthy();
  });

  it("shows activity log items", () => {
    mockHook.status = "discovering";
    mockHook.activityLog = [
      { id: "1", type: "discovery", message: "Discovered framework", timestamp: Date.now(), status: "done" },
      { id: "2", type: "discovery", message: "Discovered ports", timestamp: Date.now(), status: "done" },
    ];
    render(<AgentDiscoveryView {...defaultProps} resourceId="res-1" />);
    expect(screen.getByText("Discovered framework")).toBeTruthy();
    expect(screen.getByText("Discovered ports")).toBeTruthy();
  });

  it("shows complete state", () => {
    mockHook.status = "complete";
    render(<AgentDiscoveryView {...defaultProps} resourceId="res-1" />);
    expect(screen.getByText("Discovery Complete")).toBeTruthy();
  });
});
