"use client";

import React, { useEffect, useCallback } from "react";
import { useAgentDiscovery } from "@/hooks/useAgentDiscovery";
import DegradedModeBanner from "./DegradedModeBanner";
import InlineQuestion from "./InlineQuestion";
import DeploymentPlanPanel from "./DeploymentPlanPanel";
import type { DiscoveryState, CompletenessReport } from "@/lib/discovery-types";

interface Props {
  resourceId: string;
  repoUrl?: string;
  environment: string;
  onComplete: (state: DiscoveryState, completeness: CompletenessReport) => void;
  onCancel: () => void;
}

export default function AgentDiscoveryView({ resourceId, repoUrl, environment, onComplete, onCancel }: Props) {
  const {
    status,
    discoveryMode,
    activityLog,
    currentQuestion,
    questionsRemaining,
    discoveryState,
    completeness,
    error,
    startDiscovery,
    answerQuestion,
    cancelDiscovery,
    editField,
  } = useAgentDiscovery();

  useEffect(() => {
    if (resourceId && environment) {
      startDiscovery(resourceId, environment, repoUrl);
    }
  }, [resourceId, environment, repoUrl, startDiscovery]);

  const handleShipIt = useCallback(() => {
    if (discoveryState && completeness) {
      onComplete(discoveryState, completeness);
    }
  }, [discoveryState, completeness, onComplete]);

  const handleCancel = useCallback(() => {
    cancelDiscovery();
    onCancel();
  }, [cancelDiscovery, onCancel]);

  if (status === "idle") return null;

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

      {/* Error */}
      {status === "error" && (
        <div role="alert" style={styles.errorBanner}>
          Discovery failed: {error}
        </div>
      )}

      {/* Main layout: activity feed + plan panel */}
      <div style={styles.layout}>
        {/* Activity Feed */}
        <div style={styles.activityPanel}>
          <div style={styles.activityList}>
            {activityLog
              .filter((item) => {
                // Issue 3: Hide question activity items for fields already discovered with high confidence
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

          {/* Inline question — Issue 3: skip questions for fields already discovered with ≥80% confidence */}
          {status === "question" && currentQuestion && (() => {
            const field = currentQuestion.field;
            const existingConfidence = discoveryState?.confidence?.[field];
            if (existingConfidence && existingConfidence.score >= 0.8) {
              // Auto-answer with the discovered value instead of showing question
              const discoveredValue = discoveryState?.findings?.[field as keyof typeof discoveryState.findings];
              const autoAnswer = typeof discoveredValue === "object" && discoveredValue !== null
                ? (discoveredValue as any).framework || (discoveredValue as any).strategy || JSON.stringify(discoveredValue)
                : String(discoveredValue || "");
              if (autoAnswer) {
                // Defer the auto-answer to avoid setState during render
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
          />
        </div>
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
};
