import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import DeploymentPlanPanel from "@/components/discovery/DeploymentPlanPanel";
import type { DiscoveryState, CompletenessReport } from "@/lib/discovery-types";

const mockState: DiscoveryState = {
  findings: {
    source: "my-app",
    framework: { framework: "Next.js", runtime: "node", confidence: 0.95, evidence: ["package.json"] },
    build_strategy: { strategy: "Docker", confidence: 0.9, evidence: ["Dockerfile"] },
    target_server: { host: "prod.example.com", port: 22, user: "deploy" },
    app_port: 3000,
    health_endpoint: { path: "/health", source: "code", confidence: 0.8 },
  },
  confidence: {
    source: { source: "user-provided", detail: "Provided", score: 1.0 },
    framework: { source: "detected", detail: "From package.json", score: 0.95 },
    build_strategy: { source: "detected", detail: "From Dockerfile", score: 0.9 },
    target_server: { source: "user-provided", detail: "SSH", score: 1.0 },
    app_port: { source: "detected", detail: "Port 3000", score: 0.8 },
    health_endpoint: { source: "inferred", detail: "Common pattern", score: 0.6 },
  },
  probes_run: [],
  user_answers: {},
  completeness: { ready: true, required_coverage: 1.0, recommended_coverage: 0.33, missing_required: [], low_confidence: [] },
};

const mockCompleteness: CompletenessReport = {
  ready: true,
  required_coverage: 1.0,
  recommended_coverage: 0.33,
  missing_required: [],
  low_confidence: [],
};

describe("DeploymentPlanPanel", () => {
  it("shows placeholder when no state", () => {
    render(<DeploymentPlanPanel state={null} completeness={null} onEditField={vi.fn()} onShipIt={vi.fn()} />);
    expect(screen.getByText("Waiting for discovery data...")).toBeTruthy();
  });

  it("renders field values from state", () => {
    render(<DeploymentPlanPanel state={mockState} completeness={mockCompleteness} onEditField={vi.fn()} onShipIt={vi.fn()} />);
    expect(screen.getByText("my-app")).toBeTruthy();
    expect(screen.getByText("Next.js")).toBeTruthy();
    expect(screen.getByText("Docker")).toBeTruthy();
    expect(screen.getByText("3000")).toBeTruthy();
  });

  it("shows progress bars", () => {
    render(<DeploymentPlanPanel state={mockState} completeness={mockCompleteness} onEditField={vi.fn()} onShipIt={vi.fn()} />);
    expect(screen.getByText("100%")).toBeTruthy();
    expect(screen.getByText("33%")).toBeTruthy();
  });

  it("enables Ship It when ready", () => {
    render(<DeploymentPlanPanel state={mockState} completeness={mockCompleteness} onEditField={vi.fn()} onShipIt={vi.fn()} />);
    const btn = screen.getByText("Ship It") as HTMLButtonElement;
    expect(btn.disabled).toBe(false);
  });

  it("disables Ship It when not ready", () => {
    const notReady = { ...mockCompleteness, ready: false };
    render(<DeploymentPlanPanel state={mockState} completeness={notReady} onEditField={vi.fn()} onShipIt={vi.fn()} />);
    const btn = screen.getByText("Ship It") as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });

  it("calls onShipIt when clicked", () => {
    const onShipIt = vi.fn();
    render(<DeploymentPlanPanel state={mockState} completeness={mockCompleteness} onEditField={vi.fn()} onShipIt={onShipIt} />);
    fireEvent.click(screen.getByText("Ship It"));
    expect(onShipIt).toHaveBeenCalled();
  });

  it("collapses and expands sections", () => {
    render(<DeploymentPlanPanel state={mockState} completeness={mockCompleteness} onEditField={vi.fn()} onShipIt={vi.fn()} />);
    // Optional section is collapsed by default
    const optionalBtn = screen.getByText("Optional");
    // Click to expand
    fireEvent.click(optionalBtn);
    // Should show optional fields now
    expect(screen.getByText("Ports")).toBeTruthy();
  });
});
