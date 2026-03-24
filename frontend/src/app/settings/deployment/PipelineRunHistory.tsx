import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";
import StatusIndicator, { DeployStatus } from "./StatusIndicator";
import PipelineStepView, { PipelineStep } from "./PipelineStepView";

interface PipelineRun {
  id: string;
  run_number: number;
  status: DeployStatus;
  branch: string;
  commit_hash: string;
  commit_message: string;
  started_at: string;
  duration_seconds: number;
  failed_step?: string;
  failed_exit_code?: number;
  ticket_id?: string;
  steps?: PipelineStep[];
}

function relativeTime(iso: string): string {
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toString().padStart(2, "0")}s`;
}

export default function PipelineRunHistory() {
  const [runs, setRuns] = useState<PipelineRun[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [expandedRun, setExpandedRun] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${GATEWAY_API}/deployments/runs?limit=20`);
        if (res.ok) {
          setRuns(await res.json());
        } else if (res.status === 404) {
          setError(null); // expected — API not built yet
        } else {
          setError("Failed to load pipeline runs.");
        }
      } catch {
        // API may not exist yet
      }
      setLoaded(true);
    })();
  }, []);

  if (!loaded) return null;

  if (runs.length === 0) {
    return (
      <div style={styles.section}>
        <h4 style={styles.title}>Recent Runs</h4>
        <p style={styles.empty}>
          No pipeline runs yet. Connect a repository and add a{" "}
          <code style={styles.code}>.bond/deploy.yml</code> to get started.
        </p>
        {error && <p style={styles.errorText}>{error}</p>}
      </div>
    );
  }

  return (
    <div style={styles.section}>
      <h4 style={styles.title}>Recent Runs</h4>
      <div style={styles.list}>
        {runs.map((run) => {
          const isExpanded = expandedRun === run.id;
          return (
            <div key={run.id}>
              <div
                style={{
                  ...styles.runRow,
                  borderColor: isExpanded ? "#6c8aff" : "#1e1e2e",
                }}
                onClick={() => setExpandedRun(isExpanded ? null : run.id)}
              >
                <span style={styles.runNumber}>#{run.run_number}</span>
                <StatusIndicator status={run.status} showLabel={false} />
                <span style={styles.branch}>{run.branch}</span>
                <span style={styles.commit}>{run.commit_hash.slice(0, 7)}</span>
                <span style={styles.commitMsg}>{run.commit_message}</span>
                <span style={styles.time}>{relativeTime(run.started_at)}</span>
                <span style={styles.duration}>{formatDuration(run.duration_seconds)}</span>
              </div>

              {run.status === "failed" && run.failed_step && !isExpanded && (
                <div style={styles.failureInfo}>
                  Step failed: <strong>{run.failed_step}</strong>
                  {run.failed_exit_code != null && <> (exit {run.failed_exit_code})</>}
                  {" \u2014 "}
                  <a href="#" style={styles.link} onClick={(e) => { e.preventDefault(); e.stopPropagation(); setExpandedRun(run.id); }}>View Log</a>
                  {run.ticket_id && (
                    <>
                      {" "}
                      <a href="#" style={styles.link} onClick={(e) => e.stopPropagation()}>View Ticket #{run.ticket_id}</a>
                    </>
                  )}
                </div>
              )}

              {isExpanded && run.steps && (
                <div style={styles.expandedSteps}>
                  <PipelineStepView steps={run.steps} />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  section: { display: "flex", flexDirection: "column", gap: "8px" },
  title: { fontSize: "0.9rem", fontWeight: 600, color: "#8888a0", margin: "0 0 4px 0" },
  empty: { fontSize: "0.85rem", color: "#5a5a6e", margin: 0 },
  code: {
    backgroundColor: "#0a0a12",
    padding: "2px 6px",
    borderRadius: "4px",
    fontSize: "0.8rem",
    fontFamily: "monospace",
    color: "#6c8aff",
  },
  errorText: { fontSize: "0.8rem", color: "#ff6c8a", margin: 0 },
  list: { display: "flex", flexDirection: "column", gap: "2px" },
  runRow: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    padding: "10px 12px",
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: "6px",
    cursor: "pointer",
    transition: "border-color 0.2s",
    fontSize: "0.8rem",
  },
  runNumber: { fontWeight: 600, color: "#e0e0e8", minWidth: "32px" },
  branch: { color: "#6c8aff", fontFamily: "monospace", fontSize: "0.75rem" },
  commit: { color: "#8888a0", fontFamily: "monospace", fontSize: "0.75rem" },
  commitMsg: { color: "#e0e0e8", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" as const },
  time: { color: "#8888a0", fontSize: "0.75rem", flexShrink: 0 },
  duration: { color: "#8888a0", fontSize: "0.75rem", flexShrink: 0, minWidth: "48px", textAlign: "right" as const },
  failureInfo: {
    fontSize: "0.75rem",
    color: "#ff6c8a",
    padding: "4px 12px 8px 54px",
  },
  link: { color: "#6c8aff", textDecoration: "none", fontSize: "0.75rem" },
  expandedSteps: {
    padding: "12px 12px 12px 32px",
    borderLeftWidth: "2px", borderLeftStyle: "solid", borderLeftColor: "#1e1e2e",
    marginLeft: "16px",
    marginTop: "4px",
    marginBottom: "8px",
  },
};
