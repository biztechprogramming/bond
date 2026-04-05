import React, { useState, useEffect } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";
import StatusIndicator, { DeployStatus } from "./StatusIndicator";

export interface PipelineStep {
  name: string;
  image: string;
  status: DeployStatus;
  duration_seconds?: number;
  commands?: string[];
  secrets?: string[];
  depends_on?: string[];
  stdout_preview?: string;
  stderr_preview?: string;
  receipt_id?: string;
  exit_code?: number;
}

interface Props {
  steps: PipelineStep[];
  runId?: string;
}

function formatDuration(seconds?: number): string {
  if (seconds == null) return "-";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export default function PipelineStepView({ steps: propSteps, runId }: Props) {
  const [expandedStep, setExpandedStep] = useState<string | null>(null);
  const [liveSteps, setLiveSteps] = useState<PipelineStep[]>(propSteps);

  useEffect(() => {
    if (!runId) { setLiveSteps(propSteps); return; }

    let cancelled = false;
    const fetchRun = async () => {
      try {
        const res = await apiFetch(`${GATEWAY_API}/deployments/pipeline-code/runs/${runId}`);
        if (!res.ok || cancelled) return;
        const data = await res.json();
        const allSteps: PipelineStep[] = [];
        for (const job of data.jobs || []) {
          for (const s of job.steps || []) {
            allSteps.push({
              name: s.name,
              image: s.matrix_vars ? `matrix: ${JSON.stringify(s.matrix_vars)}` : "",
              status: (s.status === "success" ? "success" : s.status === "failed" ? "failed" : s.status === "running" ? "deploying" : "pending") as DeployStatus,
              duration_seconds: s.duration_ms != null ? Math.round(s.duration_ms / 1000) : undefined,
              stdout_preview: s.stdout?.slice(0, 500) || undefined,
              stderr_preview: s.stderr?.slice(0, 500) || undefined,
              exit_code: s.exit_code,
            });
          }
        }
        if (!cancelled && allSteps.length > 0) setLiveSteps(allSteps);
      } catch { /* fetch failed, keep existing */ }
    };

    fetchRun();
    const interval = setInterval(fetchRun, 3000);
    return () => { cancelled = true; clearInterval(interval); };
  }, [runId, propSteps]);

  const steps = liveSteps;

  if (steps.length === 0) {
    return <p style={styles.empty}>No steps defined.</p>;
  }

  return (
    <div style={styles.container}>
      <div style={styles.stepsRow}>
        {steps.map((step, i) => {
          const isExpanded = expandedStep === step.name;
          return (
            <React.Fragment key={step.name}>
              <div
                style={{
                  ...styles.stepCard,
                  ...(isExpanded ? styles.stepCardExpanded : {}),
                  borderColor: isExpanded ? "#6c8aff" : "#1e1e2e",
                }}
                onClick={() => setExpandedStep(isExpanded ? null : step.name)}
              >
                <div style={styles.stepHeader}>
                  <StatusIndicator status={step.status} showLabel={false} />
                  <span style={styles.stepName}>{step.name}</span>
                </div>
                <div style={styles.stepDuration}>{formatDuration(step.duration_seconds)}</div>
              </div>
              {i < steps.length - 1 && <span style={styles.arrow}>&rarr;</span>}
            </React.Fragment>
          );
        })}
      </div>

      {expandedStep && (() => {
        const step = steps.find((s) => s.name === expandedStep);
        if (!step) return null;
        return (
          <div style={styles.expandedPanel}>
            <div style={styles.expandedHeader}>
              <StatusIndicator status={step.status} />
              <span style={styles.expandedName}>{step.name}</span>
              <span style={styles.expandedImage}>{step.image}</span>
            </div>

            {step.depends_on && step.depends_on.length > 0 && (
              <div style={styles.detailRow}>
                <span style={styles.detailLabel}>Depends on:</span>
                <span style={styles.detailValue}>{step.depends_on.join(", ")}</span>
              </div>
            )}

            {step.commands && step.commands.length > 0 && (
              <div style={styles.detailBlock}>
                <span style={styles.detailLabel}>Commands:</span>
                <pre style={styles.codeBlock}>
                  {step.commands.map((cmd, j) => (
                    <div key={j}>{cmd}</div>
                  ))}
                </pre>
              </div>
            )}

            {step.secrets && step.secrets.length > 0 && (
              <div style={styles.detailRow}>
                <span style={styles.detailLabel}>Secrets:</span>
                <span style={styles.detailValue}>
                  {step.secrets.map((s) => "••••••").join(", ")}
                  {" "}({step.secrets.length} secret{step.secrets.length > 1 ? "s" : ""})
                </span>
              </div>
            )}

            {step.stdout_preview && (
              <div style={styles.detailBlock}>
                <span style={styles.detailLabel}>Output:</span>
                <pre style={styles.logPreview}>{step.stdout_preview}</pre>
              </div>
            )}

            {step.stderr_preview && (
              <div style={styles.detailBlock}>
                <span style={styles.detailLabel}>Errors:</span>
                <pre style={{ ...styles.logPreview, color: "#ff6c8a" }}>{step.stderr_preview}</pre>
              </div>
            )}

            {step.exit_code != null && step.exit_code !== 0 && (
              <div style={styles.detailRow}>
                <span style={styles.detailLabel}>Exit code:</span>
                <span style={{ ...styles.detailValue, color: "#ff6c8a" }}>{step.exit_code}</span>
              </div>
            )}

            {step.receipt_id && (
              <a
                href={`#receipt-${step.receipt_id}`}
                style={styles.logLink}
                onClick={(e) => e.stopPropagation()}
              >
                View Full Log
              </a>
            )}
          </div>
        );
      })()}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: "12px" },
  stepsRow: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" },
  stepCard: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: "8px",
    padding: "10px 14px",
    cursor: "pointer",
    minWidth: "80px",
    textAlign: "center" as const,
    transition: "border-color 0.2s",
  },
  stepCardExpanded: {},
  stepHeader: { display: "flex", alignItems: "center", gap: "6px", justifyContent: "center" },
  stepName: { fontSize: "0.8rem", fontWeight: 600, color: "#e0e0e8" },
  stepDuration: { fontSize: "0.7rem", color: "#8888a0", marginTop: "4px" },
  arrow: { color: "#5a5a6e", fontSize: "0.85rem", flexShrink: 0 },
  empty: { fontSize: "0.85rem", color: "#5a5a6e" },
  expandedPanel: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: "8px",
    padding: "16px",
    display: "flex",
    flexDirection: "column",
    gap: "10px",
  },
  expandedHeader: { display: "flex", alignItems: "center", gap: "8px" },
  expandedName: { fontSize: "0.9rem", fontWeight: 600, color: "#e0e0e8" },
  expandedImage: { fontSize: "0.75rem", color: "#8888a0", marginLeft: "auto" },
  detailRow: { display: "flex", alignItems: "center", gap: "8px" },
  detailBlock: { display: "flex", flexDirection: "column", gap: "4px" },
  detailLabel: { fontSize: "0.75rem", fontWeight: 600, color: "#8888a0" },
  detailValue: { fontSize: "0.8rem", color: "#e0e0e8" },
  codeBlock: {
    backgroundColor: "#0a0a12",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: "6px",
    padding: "10px",
    fontSize: "0.75rem",
    fontFamily: "monospace",
    color: "#e0e0e8",
    margin: 0,
    overflow: "auto",
    maxHeight: "120px",
  },
  logPreview: {
    backgroundColor: "#0a0a12",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: "6px",
    padding: "10px",
    fontSize: "0.75rem",
    fontFamily: "monospace",
    color: "#8888a0",
    margin: 0,
    overflow: "auto",
    maxHeight: "80px",
    whiteSpace: "pre-wrap" as const,
  },
  logLink: {
    fontSize: "0.8rem",
    color: "#6c8aff",
    textDecoration: "none",
    cursor: "pointer",
    alignSelf: "flex-start",
  },
};
