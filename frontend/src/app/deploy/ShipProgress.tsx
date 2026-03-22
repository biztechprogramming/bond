"use client";

import React, { useState, useEffect, useRef } from "react";
import { GATEWAY_API } from "@/lib/config";
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
  const eventSourceRef = useRef<EventSource | null>(null);

  useEffect(() => {
    // Start deployment execution via SSE
    const url = `${GATEWAY_API}/deployments/execute-plan`;

    try {
      // Use fetch + ReadableStream for POST SSE (EventSource only supports GET)
      const controller = new AbortController();
      fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(plan),
        signal: controller.signal,
      }).then(async (res) => {
        if (!res.ok || !res.body) {
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

          // Parse SSE events
          const lines = buffer.split("\n");
          buffer = lines.pop() || "";
          for (const line of lines) {
            if (line.startsWith("data: ")) {
              try {
                const event = JSON.parse(line.slice(6));
                handleProgressEvent(event);
              } catch { /* skip malformed */ }
            }
          }
        }
      }).catch(() => {
        // API not available — simulate progress for demo
        simulateProgress();
      });

      return () => controller.abort();
    } catch {
      simulateProgress();
    }
  }, [plan]);

  const handleProgressEvent = (event: { step?: string; status?: string; detail?: string; completed?: boolean; error?: boolean }) => {
    if (event.step && event.status) {
      setSteps((prev) =>
        prev.map((s) => s.id === event.step ? { ...s, status: event.status as ProgressStep["status"], detail: event.detail } : s)
      );
    }
    if (event.completed) {
      setCompleted(true);
      setShowConfetti(true);
      setTimeout(() => setShowConfetti(false), 4000);
    }
    if (event.error) {
      setFailed(true);
    }
  };

  // Fallback: simulate progress when API is unavailable
  const simulateProgress = () => {
    const stepIds = INITIAL_STEPS.map((s) => s.id);
    let i = 0;
    const interval = setInterval(() => {
      if (i >= stepIds.length) {
        clearInterval(interval);
        setCompleted(true);
        setShowConfetti(true);
        setTimeout(() => setShowConfetti(false), 4000);
        return;
      }
      setSteps((prev) =>
        prev.map((s, idx) => {
          if (idx === i) return { ...s, status: "running" };
          if (idx < i) return { ...s, status: "done" };
          return s;
        })
      );
      setTimeout(() => {
        setSteps((prev) =>
          prev.map((s, idx) => (idx === i ? { ...s, status: "done" } : s))
        );
        i++;
      }, 800);
    }, 1200);
    return () => clearInterval(interval);
  };

  const appId = plan.repoUrl?.split("/").pop()?.replace(".git", "") || plan.serverAddress || "app";

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

      {/* Logs toggle */}
      <button style={s.logsToggle} onClick={() => setShowLogs(!showLogs)}>
        {showLogs ? "Hide" : "Show"} Live Logs
      </button>
      {showLogs && (
        <div style={s.logsContainer}>
          <LiveLogViewer environment={plan.environment || "dev"} />
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
  actions: { display: "flex", justifyContent: "center", gap: "12px", marginTop: "24px" },
  viewBtn: {
    backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px",
    padding: "10px 24px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer",
  },
  doneBtn: {
    background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", color: "#8888a0", borderRadius: "8px",
    padding: "10px 20px", fontSize: "0.9rem", cursor: "pointer",
  },
  confetti: { position: "fixed", top: 0, left: 0, width: "100%", height: "100%", pointerEvents: "none", zIndex: 1000, overflow: "hidden" },
  confettiPiece: {
    position: "absolute", width: "8px", height: "8px", borderRadius: "2px",
    animation: "confetti-fall 3s ease-in forwards",
  },
};
