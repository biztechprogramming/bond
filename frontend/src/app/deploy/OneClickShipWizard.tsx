"use client";

import React, { useState, useCallback, useEffect, useRef } from "react";
import { useAgentsWithRelations } from "@/hooks/useSpacetimeDB";
import AgentDiscoveryView from "@/components/discovery/AgentDiscoveryView";
import type { DiscoveryState, CompletenessReport } from "@/lib/discovery-types";
import { GATEWAY_API } from "@/lib/config";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = "select-agent" | "discovery" | "allocate" | "ship";

interface DeploymentPlan {
  id: string;
  agentId?: string;
  repoPath?: string;
  framework?: string;
  buildStrategy?: string;
  environment?: string;
  buildCmd?: string;
  startCmd?: string;
  monitoringEnabled?: boolean;
  allocation?: AllocationData;
  [key: string]: unknown;
}

interface AllocationData {
  base_port: number;
  app_dir: string;
  data_dir: string;
  log_dir: string;
  config_dir: string;
  service_ports: Record<string, number>;
}

interface AllocationConflict {
  field: string;
  message: string;
  severity: "error" | "warning";
  suggestion?: string;
}

interface Props {
  onComplete: (plan: DeploymentPlan) => void;
  onCancel: () => void;
}

// ---------------------------------------------------------------------------
// Step 1: Select Agent & Repo
// ---------------------------------------------------------------------------

function SelectAgentStep({
  onNext,
  onCancel,
}: {
  onNext: (agentId: string, repoPath: string) => void;
  onCancel: () => void;
}) {
  const agents = useAgentsWithRelations();
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [selectedRepo, setSelectedRepo] = useState<string>("");

  const selectedAgent = agents.find((a) => a.id === selectedAgentId);
  const mounts = selectedAgent?.workspace_mounts ?? [];

  // Auto-select if only one agent exists
  useEffect(() => {
    if (agents.length === 1 && !selectedAgentId) {
      setSelectedAgentId(agents[0].id);
    }
  }, [agents, selectedAgentId]);

  // Reset repo when agent changes
  useEffect(() => {
    setSelectedRepo("");
  }, [selectedAgentId]);

  // Auto-select if only one mount
  useEffect(() => {
    if (mounts.length === 1 && !selectedRepo) {
      setSelectedRepo(mounts[0].host_path);
    }
  }, [mounts, selectedRepo]);

  if (agents.length === 0) {
    return (
      <div>
        <div role="alert" style={{
          padding: "16px 20px",
          backgroundColor: "rgba(255, 108, 138, 0.1)",
          borderWidth: 1, borderStyle: "solid", borderColor: "#ff6c8a",
          borderRadius: "8px", color: "#ff6c8a", fontSize: "0.9rem", marginBottom: "20px",
        }}>
          <strong>No agents configured.</strong> Create an agent first to deploy.
        </div>
        <div style={ws.actions}>
          <button style={ws.cancelBtn} onClick={onCancel}>Cancel</button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 style={ws.stepTitle}>Select Agent & Repository</h3>

      <div style={ws.field}>
        <label style={ws.label}>Agent</label>
        <select
          style={ws.input}
          value={selectedAgentId}
          onChange={(e) => setSelectedAgentId(e.target.value)}
        >
          <option value="">Select an agent...</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>
              {a.display_name || a.name}
            </option>
          ))}
        </select>
      </div>

      {selectedAgentId && (
        <div style={ws.field}>
          <label style={ws.label}>Repository</label>
          {mounts.length === 0 ? (
            <p style={{ color: "#ff6c8a", fontSize: "0.85rem", margin: 0 }}>
              This agent has no workspace mounts configured.
            </p>
          ) : (
            <select
              style={ws.input}
              value={selectedRepo}
              onChange={(e) => setSelectedRepo(e.target.value)}
            >
              <option value="">Select a repository...</option>
              {mounts.map((m) => (
                <option key={m.host_path} value={m.host_path}>
                  {m.mount_name || m.host_path}
                </option>
              ))}
            </select>
          )}
        </div>
      )}

      <div style={ws.actions}>
        <button style={ws.cancelBtn} onClick={onCancel}>Cancel</button>
        <button
          style={{ ...ws.primaryBtn, opacity: selectedAgentId && selectedRepo ? 1 : 0.5 }}
          disabled={!selectedAgentId || !selectedRepo}
          onClick={() => onNext(selectedAgentId, selectedRepo)}
        >
          Start Discovery
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Allocate
// ---------------------------------------------------------------------------

function AllocationStep({
  plan,
  onNext,
  onBack,
}: {
  plan: DeploymentPlan;
  onNext: (allocation: AllocationData) => void;
  onBack: () => void;
}) {
  const [basePort, setBasePort] = useState(3000);
  const [appDir, setAppDir] = useState("/opt/apps");
  const [dataDir, setDataDir] = useState("/var/data");
  const [logDir, setLogDir] = useState("/var/log/apps");
  const [configDir, setConfigDir] = useState("/etc/apps");
  const [servicePorts, setServicePorts] = useState<Record<string, number>>({
    app: 3000,
    postgres: 5432,
    redis: 6379,
  });
  const [conflicts, setConflicts] = useState<AllocationConflict[]>([]);
  const [loading, setLoading] = useState(true);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Fetch suggested defaults on mount
  useEffect(() => {
    const resourceId = plan.agentId || "";
    const appName = plan.repoPath?.split("/").pop() || "app";
    fetch(`${GATEWAY_API}/deployments/allocations/suggest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        resource_id: resourceId,
        app_name: appName,
        environment_name: plan.environment || "dev",
      }),
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (data) {
          if (data.base_port) setBasePort(data.base_port);
          if (data.app_dir) setAppDir(data.app_dir);
          if (data.data_dir) setDataDir(data.data_dir);
          if (data.log_dir) setLogDir(data.log_dir);
          if (data.config_dir) setConfigDir(data.config_dir);
          if (data.service_ports) setServicePorts(data.service_ports);
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [plan]);

  // Check conflicts on field changes (debounced)
  const checkConflicts = useCallback(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      fetch(`${GATEWAY_API}/deployments/allocations/check-conflicts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_port: basePort,
          app_dir: appDir,
          data_dir: dataDir,
          log_dir: logDir,
          config_dir: configDir,
          service_ports: servicePorts,
          environment_name: plan.environment || "dev",
        }),
      })
        .then((r) => (r.ok ? r.json() : { conflicts: [] }))
        .then((data) => setConflicts(Array.isArray(data.conflicts) ? data.conflicts : []))
        .catch(() => setConflicts([]));
    }, 500);
  }, [basePort, appDir, dataDir, logDir, configDir, servicePorts, plan.environment]);

  useEffect(() => {
    if (!loading) checkConflicts();
  }, [checkConflicts, loading]);

  const handleServicePortChange = (key: string, value: number) => {
    setServicePorts((prev) => ({ ...prev, [key]: value }));
  };

  const hasErrors = conflicts.some((c) => c.severity === "error");

  if (loading) {
    return (
      <div style={ws.scanning}>
        <div style={ws.spinner} />
        <p style={ws.scanText}>Fetching allocation defaults...</p>
      </div>
    );
  }

  return (
    <div>
      <h3 style={ws.stepTitle}>Port & Directory Allocation</h3>
      <p style={{ color: "#8888a0", fontSize: "0.85rem", marginBottom: "20px" }}>
        Configure ports and directories for this deployment. Defaults are suggested based on existing allocations.
      </p>

      {/* Conflicts / Warnings */}
      {conflicts.length > 0 && (
        <div style={{ marginBottom: "16px", display: "flex", flexDirection: "column", gap: "6px" }}>
          {conflicts.map((c, i) => (
            <div
              key={i}
              style={{
                padding: "8px 12px",
                borderRadius: "6px",
                fontSize: "0.83rem",
                backgroundColor: c.severity === "error" ? "rgba(255,108,138,0.1)" : "rgba(255,204,108,0.1)",
                borderWidth: "1px",
                borderStyle: "solid",
                borderColor: c.severity === "error" ? "#ff6c8a44" : "#ffcc6c44",
                color: c.severity === "error" ? "#ff6c8a" : "#ffcc6c",
              }}
            >
              <strong>{c.field}:</strong> {c.message}
              {c.suggestion && <span style={{ color: "#8888a0", marginLeft: 8 }}>Suggestion: {c.suggestion}</span>}
            </div>
          ))}
        </div>
      )}

      {/* Base Port */}
      <div style={ws.field}>
        <label style={ws.label}>Base Port</label>
        <input
          style={ws.input}
          type="number"
          value={basePort}
          onChange={(e) => setBasePort(parseInt(e.target.value) || 0)}
        />
      </div>

      {/* Directories */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "12px", marginBottom: "16px" }}>
        <div style={ws.field}>
          <label style={ws.label}>App Directory</label>
          <input style={ws.input} value={appDir} onChange={(e) => setAppDir(e.target.value)} />
        </div>
        <div style={ws.field}>
          <label style={ws.label}>Data Directory</label>
          <input style={ws.input} value={dataDir} onChange={(e) => setDataDir(e.target.value)} />
        </div>
        <div style={ws.field}>
          <label style={ws.label}>Log Directory</label>
          <input style={ws.input} value={logDir} onChange={(e) => setLogDir(e.target.value)} />
        </div>
        <div style={ws.field}>
          <label style={ws.label}>Config Directory</label>
          <input style={ws.input} value={configDir} onChange={(e) => setConfigDir(e.target.value)} />
        </div>
      </div>

      {/* Service Ports */}
      <div style={{ marginBottom: "16px" }}>
        <label style={{ ...ws.label, marginBottom: "10px", display: "block" }}>Service Ports</label>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {Object.entries(servicePorts).map(([key, port]) => (
            <div key={key} style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <span style={{ color: "#8888a0", fontSize: "0.85rem", minWidth: "80px", fontWeight: 500 }}>{key}</span>
              <input
                style={{ ...ws.input, width: "120px" }}
                type="number"
                value={port}
                onChange={(e) => handleServicePortChange(key, parseInt(e.target.value) || 0)}
              />
            </div>
          ))}
        </div>
      </div>

      <div style={ws.actions}>
        <button style={ws.cancelBtn} onClick={onBack}>&larr; Back</button>
        <button
          style={{ ...ws.primaryBtn, opacity: hasErrors ? 0.5 : 1 }}
          disabled={hasErrors}
          onClick={() =>
            onNext({
              base_port: basePort,
              app_dir: appDir,
              data_dir: dataDir,
              log_dir: logDir,
              config_dir: configDir,
              service_ports: servicePorts,
            })
          }
        >
          Continue
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 4: Ship
// ---------------------------------------------------------------------------

function ShipStep({
  plan,
  onShip,
  onBack,
}: {
  plan: DeploymentPlan;
  onShip: () => void;
  onBack: () => void;
}) {
  return (
    <div style={ws.shipContainer}>
      <h3 style={ws.stepTitle}>Ready to Ship</h3>
      <div style={ws.planSummary}>
        <p>Deploying <strong>{plan.repoPath || plan.agentId}</strong></p>
        <p>{plan.framework} &middot; {plan.buildStrategy} &middot; {plan.environment}</p>
        {plan.allocation && (
          <p style={{ fontSize: "0.85rem", color: "#6c8aff" }}>
            Port {plan.allocation.base_port} &middot; {plan.allocation.app_dir}
          </p>
        )}
      </div>
      <button style={ws.shipBtn} onClick={onShip}>
        Ship It
      </button>
      <div style={ws.actions}>
        <button style={ws.cancelBtn} onClick={onBack}>&larr; Back to Allocation</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Wizard
// ---------------------------------------------------------------------------

export default function OneClickShipWizard({ onComplete, onCancel }: Props) {
  const [step, setStep] = useState<WizardStep>("select-agent");
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [selectedRepoPath, setSelectedRepoPath] = useState("");
  const [plan, setPlan] = useState<DeploymentPlan | null>(null);
  const [allocation, setAllocation] = useState<AllocationData | null>(null);

  const handleAgentSelected = useCallback((agentId: string, repoPath: string) => {
    setSelectedAgentId(agentId);
    setSelectedRepoPath(repoPath);
    setStep("discovery");
  }, []);

  const handlePlanReady = useCallback((p: DeploymentPlan) => {
    setPlan(p);
    setStep("allocate");
  }, []);

  const handleAllocationComplete = useCallback((alloc: AllocationData) => {
    setAllocation(alloc);
    if (plan) {
      setPlan({ ...plan, allocation: alloc });
    }
    setStep("ship");
  }, [plan]);

  const handleShip = useCallback(() => {
    if (!plan) return;
    const finalPlan = { ...plan, allocation: allocation || undefined };

    // Save allocation before shipping
    if (allocation) {
      fetch(`${GATEWAY_API}/deployments/allocations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ...allocation,
          environment_name: plan.environment || "dev",
          app_name: plan.repoPath?.split("/").pop() || "app",
        }),
      }).catch(() => {});
    }

    onComplete(finalPlan);
  }, [plan, allocation, onComplete]);

  // Progress indicator
  const steps: { id: WizardStep; label: string }[] = [
    { id: "select-agent", label: "Select Agent" },
    { id: "discovery", label: "Discover" },
    { id: "allocate", label: "Allocate" },
    { id: "ship", label: "Ship" },
  ];

  const currentIdx = steps.findIndex((s) => s.id === step);

  return (
    <div style={ws.container}>
      {/* Progress bar */}
      <div style={ws.progress}>
        {steps.map((s, i) => (
          <React.Fragment key={s.id}>
            <div style={{ ...ws.progressStep, ...(i <= currentIdx ? ws.progressStepActive : {}) }}>
              <div style={{ ...ws.progressDot, ...(i <= currentIdx ? ws.progressDotActive : {}) }}>
                {i < currentIdx ? "\u2713" : i + 1}
              </div>
              <span style={ws.progressLabel}>{s.label}</span>
            </div>
            {i < steps.length - 1 && (
              <div style={{ ...ws.progressLine, ...(i < currentIdx ? ws.progressLineActive : {}) }} />
            )}
          </React.Fragment>
        ))}
      </div>

      {/* Step content */}
      {step === "select-agent" && <SelectAgentStep onNext={handleAgentSelected} onCancel={onCancel} />}
      {step === "discovery" && (
        <AgentDiscoveryView
          agentId={selectedAgentId}
          repoId={selectedRepoPath}
          environment="dev"
          onComplete={(state: DiscoveryState, _completeness: CompletenessReport) => {
            const f = state.findings;
            const str = (v: unknown): string | undefined => {
              if (v == null) return undefined;
              if (typeof v === "string") return v;
              if (typeof v === "object") {
                const o = v as Record<string, unknown>;
                return (o.framework ?? o.strategy ?? o.path ?? o.name ?? JSON.stringify(v)) as string;
              }
              return String(v);
            };
            const newPlan: DeploymentPlan = {
              id: crypto.randomUUID(),
              agentId: selectedAgentId,
              repoPath: selectedRepoPath,
              framework: str(f.framework) || "unknown",
              buildStrategy: str(f.build_strategy) || "docker",
              buildCmd: str(f.build_command),
              startCmd: str(f.start_command),
              environment: "dev",
              ...((f.app_port != null) && { appPort: Number(f.app_port) }),
              ...((f.health_endpoint != null) && { healthEndpoint: str(f.health_endpoint) }),
              ...((f.env_vars != null) && { envVars: f.env_vars }),
              ...((f.services != null) && { services: f.services }),
            };
            handlePlanReady(newPlan);
          }}
          onCancel={() => setStep("select-agent")}
        />
      )}
      {step === "allocate" && plan && (
        <AllocationStep
          plan={plan}
          onNext={handleAllocationComplete}
          onBack={() => setStep("discovery")}
        />
      )}
      {step === "ship" && plan && (
        <ShipStep plan={plan} onShip={handleShip} onBack={() => setStep("allocate")} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const ws: Record<string, React.CSSProperties> = {
  container: { maxWidth: "700px", margin: "0 auto" },
  progress: { display: "flex", alignItems: "center", justifyContent: "center", gap: "0", marginBottom: "32px" },
  progressStep: { display: "flex", flexDirection: "column", alignItems: "center", gap: "6px", minWidth: "70px" },
  progressStepActive: {},
  progressDot: {
    width: "32px", height: "32px", borderRadius: "50%", backgroundColor: "#1e1e2e", color: "#5a5a70",
    display: "flex", alignItems: "center", justifyContent: "center", fontSize: "0.85rem", fontWeight: 600,
    borderWidth: "2px", borderStyle: "solid", borderColor: "#2a2a3e",
  },
  progressDotActive: { backgroundColor: "#6c8aff", color: "#fff", borderColor: "#6c8aff" },
  progressLabel: { fontSize: "0.75rem", color: "#8888a0" },
  progressLine: { flex: 1, height: "2px", backgroundColor: "#2a2a3e", minWidth: "40px", maxWidth: "100px" },
  progressLineActive: { backgroundColor: "#6c8aff" },

  stepTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", marginBottom: "20px" },
  subheading: { fontSize: "0.9rem", fontWeight: 500, color: "#8888a0", marginBottom: "10px" },

  field: { marginBottom: "16px" },
  label: { display: "block", fontSize: "0.85rem", color: "#8888a0", marginBottom: "6px", fontWeight: 500 },
  input: {
    width: "100%", backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px",
    padding: "10px 12px", color: "#e0e0e8", fontSize: "0.95rem", outline: "none", boxSizing: "border-box",
  },

  actions: { display: "flex", justifyContent: "space-between", marginTop: "24px", gap: "12px" },
  primaryBtn: {
    backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px",
    padding: "10px 24px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer",
  },
  cancelBtn: {
    background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", color: "#8888a0", borderRadius: "8px",
    padding: "10px 20px", fontSize: "0.9rem", cursor: "pointer",
  },

  scanning: { textAlign: "center", padding: "48px 20px" },
  spinner: {
    width: "40px", height: "40px", borderWidth: "3px", borderStyle: "solid", borderColor: "#2a2a3e", borderTopColor: "#6c8aff",
    borderRadius: "50%", margin: "0 auto 16px", animation: "spin 1s linear infinite",
  },
  scanText: { fontSize: "1rem", color: "#e0e0e8", margin: "0 0 4px 0" },
  scanSubtext: { fontSize: "0.85rem", color: "#8888a0", margin: 0 },

  warning: {
    backgroundColor: "#2a2a1a", borderWidth: "1px", borderStyle: "solid", borderColor: "#aa8800", borderRadius: "8px",
    padding: "10px 14px", color: "#ffcc44", fontSize: "0.85rem", marginBottom: "16px",
  },

  planCard: {
    backgroundColor: "#12121a", borderRadius: "12px", padding: "20px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    marginBottom: "16px",
  },
  planRow: { display: "flex", justifyContent: "space-between", padding: "8px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", fontSize: "0.9rem", color: "#e0e0e8" },
  planLabel: { color: "#8888a0", fontWeight: 500 },
  code: { fontFamily: "monospace", backgroundColor: "#1e1e2e", padding: "2px 6px", borderRadius: "4px", fontSize: "0.85rem" },

  advancedToggle: {
    background: "none", borderWidth: 0, borderStyle: "none", borderColor: "transparent", color: "#6c8aff", cursor: "pointer",
    fontSize: "0.85rem", padding: "4px 0", textDecoration: "underline",
  },
  advancedSection: { marginTop: "16px", padding: "16px", backgroundColor: "#12121a", borderRadius: "8px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },

  shipContainer: { textAlign: "center", padding: "20px" },
  planSummary: { color: "#8888a0", fontSize: "0.9rem", marginBottom: "24px" },
  shipBtn: {
    backgroundColor: "#6cffa0", color: "#0a0a0f", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "12px",
    padding: "16px 48px", fontSize: "1.1rem", fontWeight: 700, cursor: "pointer",
    transition: "transform 0.15s, box-shadow 0.15s",
  },
};
