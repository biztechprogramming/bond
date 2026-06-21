import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import React from "react";

const mockReducers = {
  addAgent: vi.fn(),
  updateAgent: vi.fn(),
  deleteAgentMountsForAgent: vi.fn(),
  addAgentMount: vi.fn(),
  deleteAgentChannelsForAgent: vi.fn(),
  addAgentChannel: vi.fn(),
  deleteAgent: vi.fn(),
  setDefaultAgent: vi.fn(),
};

const mockConn = { reducers: mockReducers };
const mockApiFetch = vi.fn();

vi.mock("@/lib/config", () => ({
  BACKEND_API: "http://backend.test",
  apiFetch: (...args: any[]) => mockApiFetch(...args),
}));

vi.mock("@/hooks/useSpacetimeDB", () => ({
  useAvailableModels: () => [],
  useSpacetimeDB: (selector: any) => selector(),
}));

vi.mock("@/lib/spacetimedb-client", () => ({
  getAgents: () => [{
    id: "agent-1",
    name: "alley-cat",
    displayName: "Alley Cat",
    systemPrompt: "prompt",
    model: "anthropic/claude-sonnet-4-20250514",
    utilityModel: "anthropic/claude-sonnet-4-20250514",
    tools: "[]",
    sandboxImage: "",
    maxIterations: 10,
    isDefault: false,
    isActive: true,
  }],
  getAgentChannels: () => [{
    id: "ch-1",
    channel: "webchat",
    enabled: true,
    sandboxOverride: "",
  }],
  getAgentMounts: () => [{
    id: "mount-1",
    hostPath: "/mnt/c/dev/fastlane/mattermost",
    mountName: "mattermost",
    containerPath: "/workspace/mattermost",
    readonly: false,
  }],
  getConnection: () => mockConn,
}));

vi.mock("@/components/shared/DirBrowser", () => ({
  default: () => null,
}));

import AgentsTab from "@/app/settings/agents/AgentsTab";

describe("AgentsTab workspace mount saving", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockApiFetch.mockResolvedValue({
      ok: true,
      json: async () => ({ running: false }),
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  it("deletes persisted workspace mounts when saving after removing the last visible mount", async () => {
    render(<AgentsTab />);

    fireEvent.click(screen.getByText("Alley Cat"));
    fireEvent.click(screen.getByText("X"));
    fireEvent.click(screen.getByText("Save"));

    await waitFor(() => {
      expect(mockReducers.deleteAgentMountsForAgent).toHaveBeenCalledWith({ agentId: "agent-1" });
    });

    expect(mockReducers.addAgentMount).not.toHaveBeenCalled();
    expect(mockReducers.updateAgent).toHaveBeenCalled();
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("This will remove 1 workspace mount"));
  });
});
