"use client";
import React, { useEffect, useState } from "react";

interface DeploymentRun {
  id: string;
  script_id: string;
  script_version: string;
  environment: string;
  status: "queued" | "running" | "success" | "failed" | "cancelled";
  started_at: string;
  finished_at?: string;
  triggered_by: string;
  run_type: "deploy" | "rollback" | "health-check";
}

interface Props {
  environment?: string;
  limit?: number;
  onRollback?: (run: DeploymentRun) => void;
  onViewLogs?: (run: DeploymentRun) => void;
}

const STATUS_COLORS: Record<string, string> = {
  queued: "#5a5a70",
  running: "#6c8aff",
  success: "#6cffa0",
  failed: "#ff6c8a",
  cancelled: "#ffcc6c",
};

export type { DeploymentRun };

export default function DeploymentRunsList({ environment, limit = 20, onRollback, onViewLogs }: Props) {
  const [runs, setRuns] = useState<DeploymentRun[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const url = environment
      ? `/api/v1/deployments/runs?environment=${environment}`
      : "/api/v1/deployments/runs";
    fetch(url)
      .then((r) => r.ok ? r.json() : [])
      .then((data) => setRuns(Array.isArray(data) ? data.slice(0, limit) : []))
      .catch(() => setRuns([]))
      .finally(() => setLoading(false));
  }, [environment, limit]);

  // Also poll every 10s for updates
  useEffect(() => {
    const interval = setInterval(() => {
      const url = environment
        ? `/api/v1/deployments/runs?environment=${environment}`
        : "/api/v1/deployments/runs";
      fetch(url)
        .then((r) => r.ok ? r.json() : [])
        .then((data) => setRuns(Array.isArray(data) ? data.slice(0, limit) : []))
        .catch(() => {});
    }, 10000);
    return () => clearInterval(interval);
  }, [environment, limit]);

  if (loading) return <div style={{ color: "#8888a0", padding: "1rem" }}>Loading deployments...</div>;
  if (runs.length === 0) return <div style={{ color: "#8888a0", padding: "1rem" }}>No deployments yet.</div>;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
      {runs.map((run) => (
        <div
          key={run.id}
          style={{
            display: "flex",
            alignItems: "center",
            gap: "1rem",
            padding: "0.75rem 1rem",
            background: "#1a1a2e",
            borderRadius: 8,
            border: "1px solid #2a2a3e",
          }}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: STATUS_COLORS[run.status] || "#5a5a70",
              flexShrink: 0,
            }}
          />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ color: "#e0e0e8", fontSize: "0.9rem", fontWeight: 600 }}>
              {run.script_id}
              <span style={{ color: "#5a5a70", fontWeight: 400, marginLeft: 8 }}>v{run.script_version}</span>
            </div>
            <div style={{ color: "#8888a0", fontSize: "0.75rem", marginTop: 2 }}>
              {run.environment} · {run.run_type} · {new Date(run.started_at).toLocaleString()}
            </div>
          </div>
          <span
            style={{
              color: STATUS_COLORS[run.status],
              fontSize: "0.8rem",
              fontWeight: 600,
              textTransform: "uppercase",
            }}
          >
            {run.status}
          </span>
          {onViewLogs && (
            <button
              onClick={() => onViewLogs(run)}
              style={{
                background: "transparent",
                border: "1px solid #3a3a50",
                color: "#6c8aff",
                padding: "4px 10px",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: "0.75rem",
              }}
            >
              Logs
            </button>
          )}
          {onRollback && run.status === "success" && run.run_type === "deploy" && (
            <button
              onClick={() => onRollback(run)}
              style={{
                background: "transparent",
                border: "1px solid #ff6c8a33",
                color: "#ff6c8a",
                padding: "4px 10px",
                borderRadius: 4,
                cursor: "pointer",
                fontSize: "0.75rem",
              }}
            >
              Rollback
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
