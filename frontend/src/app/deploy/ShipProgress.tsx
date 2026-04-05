"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";
import LiveLogViewer from "../settings/deployment/LiveLogViewer";

interface DeploymentPlan {
  id: string;
  repoUrl?: string;
  serverAddress?: string;
  framework?: string;
  buildStrategy?: string;
  environment?: string;
  [key: string]: unknown;
}

interface Props {
  plan: DeploymentPlan;
  onDone: () => void;
  onViewApp: (id: string) => void;
}

interface ProgressStep {
  id: string;
  label: string;
  status: "pending" | "running" | "done" | "error";
  detail?: string;
}

const INITIAL_STEPS: ProgressStep[] = [
  { id: "validate", label: "Validating configuration", status: "pending" },
  { id: "build", label: "Building application", status: "pending" },
  { id: "push", label: "Pushing artifacts", status: "pending" },
  { id: "deploy", label: "Deploying to environment", status: "pending" },
  { id: "health", label: "Health check", status: "pending" },
  { id: "monitor", label: "Setting up monitoring", status: "pending" },
];

const STATUS_ICON: Record<string, string> = {
  pending: "\u25cb",
  running: "\u25cf",
  done: "\u2713",
  error: "\u2717",
};

const STATUS_COLOR: Record<string, string> = {
  pending: "#5a5a70",
  running: "#6c8aff",
  done: "#6cffa0",
  error: "#ff6c8a",
};

export default function ShipProgress({ plan, onDone, onViewApp }: Props) {
  const [steps, setSteps] = useState<ProgressStep[]>(INITIAL_STEPS);
  const [completed, setCompleted] = useState(false);
  const [failed, setFailed] = useState(false);
  const [showLogs, setShowLogs] = useState(false);
  const [showConfetti, setShowConfetti] = useState(false);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const handleProgressEvent = useCallback((event: {
    step?: string;
    status?: string;
    detail?: string;
    completed?: boolean;
    error?: boolean;
    log?: string;
    run_id?: string;
    steps?: Array<{ id: string; label: string; status: string; detail?: string }>;
  }) => {
    // Update run ID if provided
    if (event.run_id) {
      setRunId(event.run_id);
    }

    // Bulk step update
    if (event.steps) {
      setSteps((prev) =>
        prev.map((s) => {
          const update = event.steps!.find((u) => u.id === s.id);
          return update ? { ...s, status: update.status as ProgressStep["status"], detail: update.detail } : s;
        })
      );
    }

    // Single step update
    if (event.step && event.status) {
      setSteps((prev) =>
        prev.map((s) => s.id === event.step ? { ...s, status: event.status as ProgressStep["status"], detail: event.detail } : s)
      );
    }

    // Log line
    if (event.log) {
      setLogLines((prev) => [...prev, event.log!]);
    }

    if (event.completed) {
      setCompleted(true);
      setShowConfetti(true);
      setTimeout(() => setShowConfetti(false), 4000);
    }
    if (event.error) {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    // Start deployment execution via SSE
    const url = `${GATEWAY_API}/deployments/execute-plan`;
    const controller = new AbortController();
    abortRef.current = controller;

    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(plan),
      signal: controller.signal,
    })
      .then(async (res) => {
        if (!res.ok || !res.body) {
          // Try to get error details
          const text = await res.text().catch(() => "unknown error");
          console.error("Deploy execute-plan failed:", res.status, text);
          setLogLines((prev) => [...prev, `[ERROR] Deploy API returned ${res.status}: ${text}`]);
          setFailed(true);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });

          // Parse SSE events: split on double newlines
          const parts = buffer.split("\n\n");
          buffer = parts.pop() || "";
          for (const part of parts) {
            const lines = part.split("\n");
            for (const line of lines) {
              if (line.startsWith("data: ")) {
                try {
                  const event = JSON.parse(line.slice(6));
                  handleProgressEvent(event);
                } catch {
                  /* skip malformed JSON */
                }
              }
            }
          }
        }

        // Process any remaining buffer
        if (buffer.trim()) {
          const lines = buffer.split("\n");
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const event = JSON.parse(line.slice(6));
                handleProgressEvent(event);
              } catch {
                /* skip */
              }
            }
          }
        }
      })
      .catch((err) => {
        if (err.name === "AbortError") return;
        console.error("Deploy SSE connection failed:", err.message);
        setLogLines((prev) => [...prev, `[ERROR] Deploy connection failed: ${err.message}`]);
        setFailed(true);
      });

    return () => {
      controller.abort();
    };
  }, [plan, handleProgressEvent]);

  const handleCancel = async () => {
    if (!runId) return;
    setCancelling(true);
    try {
      await apiFetch(`${GATEWAY_API}/deployments/runs/${runId}/cancel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      // Abort the SSE connection
      if (abortRef.current) abortRef.current.abort();
      setFailed(true);
      setSteps((prev) =>
        prev.map((s) => s.status === "running" ? { ...s, status: "error", detail: "Cancelled" } : s)
      );
    } catch {
      // If cancel fails, still allow user to go back
    } finally {
      setCancelling(false);
    }
  };

  const appId = plan.repoUrl?.split("/").pop()?.replace(".git", "") || plan.serverAddress || "app";
  const isRunning = !completed && !failed;

  return (
    <div style={s.container}>
      {showConfetti && (
        <div style={s.confetti} aria-hidden="true">
          {Array.from({ length: 30 }).map((_, i) => (
            <span
              key={i}
              style={{
                ...s.confettiPiece,
                left: `${Math.random() * 100}%`,
                animationDelay: `${Math.random() * 2}s`,
                backgroundColor: ["#6c8aff", "#6cffa0", "#ffcc6c", "#ff6c8a", "#e0e0e8"][i % 5],
              }}
            />
          ))}
        </div>
      )}

      <h2 style={s.title}>{completed ? "Deployment Complete!" : failed ? "Deployment Failed" : "Deploying..."}</h2>

      {/* Steps */}
      <div style={s.steps}>
        {steps.map((step) => (
          <div key={step.id} style={s.stepRow}>
            <span style={{ ...s.stepIcon, color: STATUS_COLOR[step.status] }}>{STATUS_ICON[step.status]}</span>
            <span style={{ ...s.stepLabel, color: step.status === "pending" ? "#5a5a70" : "#e0e0e8" }}>
              {step.label}
            </span>
            {step.detail && <span style={s.stepDetail}>{step.detail}</span>}
          </div>
        ))}
      </div>

      {/* Cancel button during deployment */}
      {isRunning && (
        <div style={{ textAlign: "center", marginBottom: "12px" }}>
          <button
            style={s.cancelBtn}
            onClick={handleCancel}
            disabled={cancelling}
          >
            {cancelling ? "Cancelling..." : "Cancel Deployment"}
          </button>
        </div>
      )}

      {/* Logs toggle */}
      <button style={s.logsToggle} onClick={() => setShowLogs(!showLogs)}>
        {showLogs ? "Hide" : "Show"} Live Logs
      </button>
      {showLogs && (
        <div style={s.logsContainer}>
          {logLines.length > 0 ? (
            <div style={s.logViewer}>
              {logLines.map((line, i) => (
                <div key={i} style={s.logLine}>{line}</div>
              ))}
            </div>
          ) : (
            <LiveLogViewer environment={plan.environment || "dev"} />
          )}
        </div>
      )}

      {/* Completion actions */}
      {(completed || failed) && (
        <div style={s.actions}>
          {completed && (
            <button style={s.viewBtn} onClick={() => onViewApp(appId)}>
              View App
            </button>
          )}
          <button style={s.doneBtn} onClick={onDone}>
            {completed ? "Back to Dashboard" : "Back"}
          </button>
        </div>
      )}

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
        @keyframes confetti-fall {
          0% { transform: translateY(-100vh) rotate(0deg); opacity: 1; }
          100% { transform: translateY(100vh) rotate(720deg); opacity: 0; }
        }
      `}</style>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  container: { maxWidth: "600px", margin: "0 auto", position: "relative" },
  title: { fontSize: "1.3rem", fontWeight: 700, color: "#e0e0e8", textAlign: "center", marginBottom: "28px" },
  steps: { display: "flex", flexDirection: "column", gap: "12px", marginBottom: "24px" },
  stepRow: { display: "flex", alignItems: "center", gap: "12px", padding: "10px 16px", backgroundColor: "#12121a", borderRadius: "8px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },
  stepIcon: { fontSize: "1.1rem", fontWeight: 700, width: "20px", textAlign: "center" },
  stepLabel: { fontSize: "0.9rem", fontWeight: 500, flex: 1 },
  stepDetail: { fontSize: "0.8rem", color: "#8888a0" },
  logsToggle: {
    background: "none", borderWidth: 0, borderStyle: "none", borderColor: "transparent", color: "#6c8aff", cursor: "pointer",
    fontSize: "0.85rem", padding: "4px 0", textDecoration: "underline", display: "block", margin: "0 auto",
  },
  logsContainer: { marginTop: "12px", maxHeight: "300px", overflow: "auto" },
  logViewer: {
    backgroundColor: "#0a0a0f", borderRadius: "8px", padding: "12px", fontFamily: "monospace", fontSize: "0.8rem",
    color: "#8888a0", maxHeight: "280px", overflowY: "auto",
  },
  logLine: { padding: "2px 0", whiteSpace: "pre-wrap", wordBreak: "break-all" },
  actions: { display: "flex", justifyContent: "center", gap: "12px", marginTop: "24px" },
  viewBtn: {
    backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px",
    padding: "10px 24px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer",
  },
  doneBtn: {
    background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", color: "#8888a0", borderRadius: "8px",
    padding: "10px 20px", fontSize: "0.9rem", cursor: "pointer",
  },
  cancelBtn: {
    background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#ff6c8a44", color: "#ff6c8a", borderRadius: "8px",
    padding: "8px 20px", fontSize: "0.85rem", cursor: "pointer",
  },
  confetti: { position: "fixed", top: 0, left: 0, width: "100%", height: "100%", pointerEvents: "none", zIndex: 1000, overflow: "hidden" },
  confettiPiece: {
    position: "absolute", width: "8px", height: "8px", borderRadius: "2px",
    animation: "confetti-fall 3s ease-in forwards",
  },
};
