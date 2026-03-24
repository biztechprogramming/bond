"use client";

import React, { useState, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";
import { useResources } from "@/hooks/useSpacetimeDB";
import BuildStrategyDetector from "../settings/deployment/BuildStrategyDetector";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = "connect" | "discovery" | "ship";
type ConnectMode = "repo" | "server";

interface DeploymentPlan {
  id: string;
  repoUrl?: string;
  serverAddress?: string;
  framework?: string;
  buildStrategy?: string;
  environment?: string;
  buildCmd?: string;
  startCmd?: string;
  monitoringEnabled?: boolean;
  [key: string]: unknown;
}

interface Props {
  onComplete: (plan: DeploymentPlan) => void;
  onCancel: () => void;
}

// ---------------------------------------------------------------------------
// Step 1: Connect
// ---------------------------------------------------------------------------

function ConnectStep({
  onNext,
  onCancel,
}: {
  onNext: (mode: ConnectMode, data: { repoUrl?: string; serverAddress?: string; sshKeyId?: string }) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = useState<ConnectMode | null>(null);
  const [repoUrl, setRepoUrl] = useState("");
  const [serverAddress, setServerAddress] = useState("");
  const [sshKeyId, setSshKeyId] = useState("");
  const resources = useResources();

  // Existing servers for quick selection
  const existingServers = resources.filter((r) => r.resourceType === "server");

  if (!mode) {
    return (
      <div>
        <h3 style={ws.stepTitle}>What are you deploying?</h3>
        <div style={ws.cardRow}>
          <div style={ws.optionCard} onClick={() => setMode("repo")} role="button" tabIndex={0}>
            <div style={ws.optionIcon}>&#128230;</div>
            <div style={ws.optionLabel}>Connect Repository</div>
            <div style={ws.optionDesc}>Deploy from a Git repository URL</div>
          </div>
          <div style={ws.optionCard} onClick={() => setMode("server")} role="button" tabIndex={0}>
            <div style={ws.optionIcon}>&#128421;</div>
            <div style={ws.optionLabel}>Connect Server</div>
            <div style={ws.optionDesc}>Deploy to a server via SSH</div>
          </div>
        </div>

        {existingServers.length > 0 && (
          <div style={{ marginTop: "24px" }}>
            <h4 style={ws.subheading}>Or select an existing resource:</h4>
            <div style={ws.existingList}>
              {existingServers.map((srv) => (
                <button
                  key={srv.id}
                  style={ws.existingItem}
                  onClick={() => onNext("server", { serverAddress: srv.name })}
                >
                  {srv.displayName || srv.name}
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={ws.actions}>
          <button style={ws.cancelBtn} onClick={onCancel}>Cancel</button>
        </div>
      </div>
    );
  }

  if (mode === "repo") {
    return (
      <div>
        <h3 style={ws.stepTitle}>Connect Repository</h3>
        <div style={ws.field}>
          <label style={ws.label}>Repository URL</label>
          <input
            style={ws.input}
            placeholder="https://github.com/org/repo.git"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
          />
        </div>
        <div style={ws.actions}>
          <button style={ws.cancelBtn} onClick={() => setMode(null)}>&larr; Back</button>
          <button
            style={{ ...ws.primaryBtn, opacity: repoUrl ? 1 : 0.5 }}
            disabled={!repoUrl}
            onClick={() => onNext("repo", { repoUrl })}
          >
            Continue
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <h3 style={ws.stepTitle}>Connect Server</h3>
      <div style={ws.field}>
        <label style={ws.label}>Server Address</label>
        <input
          style={ws.input}
          placeholder="192.168.1.100 or hostname"
          value={serverAddress}
          onChange={(e) => setServerAddress(e.target.value)}
        />
      </div>
      <div style={ws.field}>
        <label style={ws.label}>SSH Key (optional)</label>
        <input
          style={ws.input}
          placeholder="SSH key ID or paste key"
          value={sshKeyId}
          onChange={(e) => setSshKeyId(e.target.value)}
        />
      </div>
      <div style={ws.actions}>
        <button style={ws.cancelBtn} onClick={() => setMode(null)}>&larr; Back</button>
        <button
          style={{ ...ws.primaryBtn, opacity: serverAddress ? 1 : 0.5 }}
          disabled={!serverAddress}
          onClick={() => onNext("server", { serverAddress, sshKeyId })}
        >
          Continue
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 2: Discovery
// ---------------------------------------------------------------------------

function DiscoveryStep({
  connectData,
  onNext,
  onBack,
}: {
  connectData: { repoUrl?: string; serverAddress?: string; sshKeyId?: string };
  onNext: (plan: DeploymentPlan) => void;
  onBack: () => void;
}) {
  const [scanning, setScanning] = useState(true);
  const [plan, setPlan] = useState<DeploymentPlan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  // Kick off discovery
  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        setScanning(true);
        const res = await fetch(`${GATEWAY_API}/deployments/generate-plan`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(connectData),
        });
        if (!res.ok) throw new Error(`Discovery failed: ${res.statusText}`);
        const data = await res.json();
        if (!cancelled) {
          setPlan({ id: data.id || crypto.randomUUID(), ...data, ...connectData });
          setScanning(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Discovery failed");
          setScanning(false);
          // Create a basic fallback plan so user can proceed
          setPlan({
            id: crypto.randomUUID(),
            ...connectData,
            framework: "Unknown",
            buildStrategy: "auto",
            environment: "dev",
          });
        }
      }
    })();
    return () => { cancelled = true; };
  }, [connectData]);

  if (scanning) {
    return (
      <div style={ws.scanning}>
        <div style={ws.spinner} />
        <p style={ws.scanText}>Scanning {connectData.repoUrl || connectData.serverAddress}...</p>
        <p style={ws.scanSubtext}>Detecting framework, build strategy, and dependencies</p>
      </div>
    );
  }

  return (
    <div>
      <h3 style={ws.stepTitle}>Deployment Plan</h3>
      {error && <div style={ws.warning}>{error} — showing basic plan. Adjust as needed.</div>}

      {plan && (
        <div style={ws.planCard}>
          <div style={ws.planRow}><span style={ws.planLabel}>Source</span><span>{plan.repoUrl || plan.serverAddress}</span></div>
          <div style={ws.planRow}><span style={ws.planLabel}>Framework</span><span>{plan.framework || "Auto-detect"}</span></div>
          <div style={ws.planRow}><span style={ws.planLabel}>Build Strategy</span><span>{plan.buildStrategy || "auto"}</span></div>
          <div style={ws.planRow}><span style={ws.planLabel}>Environment</span><span>{plan.environment || "dev"}</span></div>
          {plan.buildCmd && <div style={ws.planRow}><span style={ws.planLabel}>Build Command</span><span style={ws.code}>{plan.buildCmd}</span></div>}
          {plan.startCmd && <div style={ws.planRow}><span style={ws.planLabel}>Start Command</span><span style={ws.code}>{plan.startCmd}</span></div>}
        </div>
      )}

      <button style={ws.advancedToggle} onClick={() => setShowAdvanced(!showAdvanced)}>
        {showAdvanced ? "Hide" : "Show"} Advanced Options
      </button>

      {showAdvanced && plan && (
        <div style={ws.advancedSection}>
          {connectData.repoUrl && (
            <BuildStrategyDetector
              repoUrl={connectData.repoUrl!}
              strategy="auto"
              onDetected={(result: { suggested_build_cmd: string; suggested_start_cmd: string }) => {
                setPlan((prev) => prev ? { ...prev, buildCmd: result.suggested_build_cmd, startCmd: result.suggested_start_cmd } : prev);
              }}
            />
          )}
        </div>
      )}

      <div style={ws.actions}>
        <button style={ws.cancelBtn} onClick={onBack}>&larr; Back</button>
        <button style={ws.primaryBtn} onClick={() => plan && onNext(plan)}>
          Continue to Ship
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Step 3: Ship
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
        <p>Deploying <strong>{plan.repoUrl || plan.serverAddress}</strong></p>
        <p>{plan.framework} &middot; {plan.buildStrategy} &middot; {plan.environment}</p>
      </div>
      <button style={ws.shipBtn} onClick={onShip}>
        Ship It
      </button>
      <div style={ws.actions}>
        <button style={ws.cancelBtn} onClick={onBack}>&larr; Back to Plan</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Wizard
// ---------------------------------------------------------------------------

export default function OneClickShipWizard({ onComplete, onCancel }: Props) {
  const [step, setStep] = useState<WizardStep>("connect");
  const [connectData, setConnectData] = useState<{ repoUrl?: string; serverAddress?: string; sshKeyId?: string }>({});
  const [plan, setPlan] = useState<DeploymentPlan | null>(null);

  const handleConnect = useCallback((_mode: ConnectMode, data: typeof connectData) => {
    setConnectData(data);
    setStep("discovery");
  }, []);

  const handlePlanReady = useCallback((p: DeploymentPlan) => {
    setPlan(p);
    setStep("ship");
  }, []);

  const handleShip = useCallback(() => {
    if (plan) onComplete(plan);
  }, [plan, onComplete]);

  // Progress indicator
  const steps: { id: WizardStep; label: string }[] = [
    { id: "connect", label: "Connect" },
    { id: "discovery", label: "Discover" },
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
      {step === "connect" && <ConnectStep onNext={handleConnect} onCancel={onCancel} />}
      {step === "discovery" && (
        <DiscoveryStep connectData={connectData} onNext={handlePlanReady} onBack={() => setStep("connect")} />
      )}
      {step === "ship" && plan && (
        <ShipStep plan={plan} onShip={handleShip} onBack={() => setStep("discovery")} />
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

  cardRow: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px", marginBottom: "20px" },
  optionCard: {
    backgroundColor: "#12121a", borderRadius: "12px", padding: "24px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    cursor: "pointer", textAlign: "center", transition: "border-color 0.2s",
  },
  optionIcon: { fontSize: "2rem", marginBottom: "8px" },
  optionLabel: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8", marginBottom: "4px" },
  optionDesc: { fontSize: "0.8rem", color: "#8888a0" },

  existingList: { display: "flex", gap: "8px", flexWrap: "wrap" },
  existingItem: {
    backgroundColor: "#1e1e2e", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px",
    padding: "8px 14px", fontSize: "0.85rem", cursor: "pointer",
  },

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
