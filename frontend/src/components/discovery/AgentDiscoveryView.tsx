"use client";

import React, { useState, useEffect, useCallback } from "react";
import { useAgentDiscovery } from "@/hooks/useAgentDiscovery";
import DegradedModeBanner from "./DegradedModeBanner";
import InlineQuestion from "./InlineQuestion";
import DeploymentPlanPanel from "./DeploymentPlanPanel";
import type { DiscoveryState, CompletenessReport } from "@/lib/discovery-types";

interface Props {
  agentId: string;
  repoId?: string;
  environment: string;
  onComplete: (state: DiscoveryState, completeness: CompletenessReport) => void;
  onCancel: () => void;
}

export default function AgentDiscoveryView({ agentId, repoId, environment, onComplete, onCancel }: Props) {
  const {
    status,
    discoveryMode,
    activityLog,
    rawEvents,
    currentQuestion,
    questionsRemaining,
    discoveryState,
    completeness,
    error,
    startDiscovery,
    answerQuestion,
    cancelDiscovery,
    editField,
    forceComplete,
  } = useAgentDiscovery();

  const [rawPanelOpen, setRawPanelOpen] = useState(true);
  const [startError, setStartError] = useState<string | null>(null);

  // Start discovery on mount (only when agentId is valid)
  useEffect(() => {
    if (agentId && environment) {
      startDiscovery("", environment, undefined, agentId, repoId).catch((err: unknown) => {
        setStartError(err instanceof Error ? err.message : String(err));
      });
    }
  }, [agentId, repoId, environment, startDiscovery]);

  const handleShipIt = useCallback(() => {
    if (discoveryState && completeness) {
      onComplete(discoveryState, completeness);
    }
  }, [discoveryState, completeness, onComplete]);

  const handleCancel = useCallback(() => {
    cancelDiscovery();
    onCancel();
  }, [cancelDiscovery, onCancel]);

  // If agentId is falsy, show an error — never fall back silently
  if (!agentId) {
    return (
      <div role="alert" style={styles.errorBanner}>
        <strong>No agent selected.</strong> Please go back and select an agent.
      </div>
    );
  }

  if (status === "idle" && !startError) return null;

  return (
    <div style={styles.wrapper}>
      {/* Header */}
      <div style={styles.header}>
        <h2 style={styles.title}>
          {status === "connecting" && "Connecting..."}
          {status === "discovering" && "Discovering your stack..."}
          {status === "degraded" && "Discovering (limited)..."}
          {status === "question" && "Bond needs your input"}
          {status === "complete" && "Discovery Complete"}
          {status === "error" && "Discovery Error"}
        </h2>
        {status !== "complete" && status !== "error" && (
          <button style={styles.cancelBtn} onClick={handleCancel}>Cancel</button>
        )}
      </div>

      {/* Degraded mode banner */}
      {(status === "degraded" || (status === "discovering" && discoveryMode !== "full")) && (
        <DegradedModeBanner mode={discoveryMode} />
      )}

      {/* Start error — e.g. startDiscovery() promise rejected */}
      {startError && (
        <div role="alert" style={styles.errorBanner}>
          <strong>Failed to start discovery</strong>
          <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", fontSize: "0.8rem", color: "#ff6c8a" }}>{startError}</pre>
        </div>
      )}

      {/* Error — show FULL error message */}
      {status === "error" && (
        <div role="alert" style={styles.errorBanner}>
          <strong>Discovery failed</strong>
          <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", fontSize: "0.8rem", color: "#ff6c8a" }}>{error || "Unknown error"}</pre>
        </div>
      )}

      {/* Complete but empty state */}
      {status === "complete" && (!discoveryState || Object.keys(discoveryState.findings || {}).length === 0) && (
        <div role="alert" style={styles.errorBanner}>
          <strong>Discovery completed but returned no data</strong>
          <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", fontSize: "0.8rem", color: "#ff6c8a" }}>
            discoveryState: {JSON.stringify(discoveryState, null, 2)}
          </pre>
        </div>
      )}

      {/* Main layout: activity feed + plan panel */}
      <div style={styles.layout}>
        {/* Activity Feed */}
        <div style={styles.activityPanel}>
          <div style={styles.activityList}>
            {activityLog
              .filter((item) => {
                // Hide question activity items for fields already discovered with high confidence
                if (item.type === "question" && item.field) {
                  const discoveredItem = activityLog.find(
                    a => a.type === "discovery" && a.field === item.field && a.confidence && a.confidence.score >= 0.8
                  );
                  if (discoveredItem) return false;
                }
                return true;
              })
              .map((item) => (
              <div key={item.id} style={styles.activityItem}>
                <span style={styles.activityIcon}>
                  {item.status === "done" ? "\u2713" : item.status === "error" ? "\u2717" : item.type === "question" ? "?" : "\u2022"}
                </span>
                <span style={{
                  ...styles.activityText,
                  color: item.status === "error" ? "#ff6c8a" : item.status === "done" ? "#6cffa0" : "#e0e0e8",
                }}>
                  {item.message}
                </span>
                {item.confidence && (
                  <span style={styles.activityConf}>
                    {Math.round(item.confidence.score * 100)}%
                  </span>
                )}
              </div>
            ))}
            {(status === "connecting" || status === "discovering" || status === "degraded") && (
              <div style={styles.activityItem}>
                <span style={{ ...styles.activityIcon, color: "#6c8aff" }}>{"\u25cf"}</span>
                <span style={styles.activityText}>
                  {status === "connecting" ? "Connecting to discovery agent..." : "Scanning..."}
                </span>
              </div>
            )}
          </div>

          {/* Inline question — skip questions for fields already discovered with >=80% confidence */}
          {status === "question" && currentQuestion && (() => {
            const field = currentQuestion.field;
            const existingConfidence = discoveryState?.confidence?.[field];
            if (existingConfidence && existingConfidence.score >= 0.8) {
              const discoveredValue = discoveryState?.findings?.[field as keyof typeof discoveryState.findings];
              const autoAnswer = typeof discoveredValue === "object" && discoveredValue !== null
                ? (discoveredValue as any).framework || (discoveredValue as any).strategy || JSON.stringify(discoveredValue)
                : String(discoveredValue || "");
              if (autoAnswer) {
                setTimeout(() => answerQuestion(field, autoAnswer), 0);
                return null;
              }
            }
            return (
              <div style={styles.questionArea}>
                <InlineQuestion question={currentQuestion} onAnswer={answerQuestion} />
              </div>
            );
          })()}
        </div>

        {/* Plan Panel */}
        <div style={styles.planPanel}>
          <DeploymentPlanPanel
            state={discoveryState}
            completeness={completeness}
            onEditField={editField}
            onShipIt={handleShipIt}
            onForceComplete={forceComplete}
            isDiscovering={status === "discovering" || status === "degraded" || status === "question"}
          />
        </div>
      </div>

      {/* Raw Events Debug Panel */}
      <div style={styles.rawPanel}>
        <button
          style={styles.rawToggle}
          onClick={() => setRawPanelOpen((v) => !v)}
        >
          {rawPanelOpen ? "▼" : "▶"} Raw SSE Events ({rawEvents.length})
        </button>
        {rawPanelOpen && (
          <pre style={styles.rawContent}>
            {rawEvents.length === 0
              ? "No SSE events received yet."
              : rawEvents.map((evt, i) => `[${new Date(evt.receivedAt).toISOString()}]\n${JSON.stringify(evt.data, null, 2)}`).join("\n\n")}
          </pre>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: "flex",
    flexDirection: "column",
    gap: 16,
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  title: {
    fontSize: "1.15rem",
    fontWeight: 700,
    color: "#e0e0e8",
    margin: 0,
  },
  cancelBtn: {
    backgroundColor: "transparent",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#2a2a3e",
    borderRadius: 6,
    color: "#8888a0",
    fontSize: "0.8rem",
    padding: "6px 14px",
    cursor: "pointer",
  },
  errorBanner: {
    padding: "10px 16px",
    backgroundColor: "rgba(255, 108, 138, 0.1)",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#ff6c8a",
    borderRadius: 8,
    color: "#ff6c8a",
    fontSize: "0.85rem",
  },
  layout: {
    display: "flex",
    gap: 16,
    flexWrap: "wrap" as const,
  },
  activityPanel: {
    flex: "1 1 400px",
    minWidth: 300,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  activityList: {
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  activityItem: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 12px",
    backgroundColor: "#12121a",
    borderRadius: 6,
    fontSize: "0.83rem",
  },
  activityIcon: {
    width: 16,
    textAlign: "center" as const,
    color: "#6cffa0",
    fontWeight: 700,
    fontSize: "0.85rem",
  },
  activityText: { flex: 1, color: "#e0e0e8" },
  activityConf: { fontSize: "0.75rem", color: "#8888a0" },
  questionArea: { marginTop: 4 },
  planPanel: {
    flex: "1 1 350px",
    minWidth: 300,
  },
  rawPanel: {
    marginTop: 8,
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#2a2a3e",
    borderRadius: 8,
    backgroundColor: "#0a0a12",
    overflow: "hidden",
  },
  rawToggle: {
    width: "100%",
    padding: "8px 14px",
    backgroundColor: "transparent",
    border: "none",
    color: "#8888a0",
    fontSize: "0.8rem",
    textAlign: "left" as const,
    cursor: "pointer",
  },
  rawContent: {
    padding: "8px 14px",
    margin: 0,
    color: "#a0a0b8",
    fontSize: "0.72rem",
    lineHeight: 1.5,
    maxHeight: 400,
    overflow: "auto",
    whiteSpace: "pre-wrap" as const,
    wordBreak: "break-all" as const,
  },
};
