"use client";

import React, { useState, useEffect, useCallback, useRef } from "react";
import { useAgentDiscovery, type ConversationMessage } from "@/hooks/useAgentDiscovery";
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
  onStateChange?: (state: DiscoveryState | null, conversationMessages: ConversationMessage[]) => void;
}

// ---------------------------------------------------------------------------
// Conversation Message Bubble
// ---------------------------------------------------------------------------

function ConversationBubble({ msg }: { msg: ConversationMessage }) {
  const [expanded, setExpanded] = useState(false);

  if (msg.type === "tool_call") {
    let parsed: { tool?: string; args?: Record<string, unknown> } | null = null;
    try { parsed = JSON.parse(msg.content); } catch { /* not JSON */ }
    const toolName = parsed?.tool || msg.toolName || "tool";
    return (
      <div style={bubbleStyles.toolCall}>
        <button style={bubbleStyles.toolToggle} onClick={() => setExpanded(!expanded)}>
          <span style={bubbleStyles.toolIcon}>{expanded ? "\u25BC" : "\u25B6"}</span>
          <span style={bubbleStyles.toolLabel}>Calling {toolName}</span>
        </button>
        {expanded && parsed?.args && (
          <pre style={bubbleStyles.toolArgs}>{JSON.stringify(parsed.args, null, 2)}</pre>
        )}
      </div>
    );
  }

  if (msg.type === "tool_result") {
    const toolName = msg.toolName || "tool";
    const content = msg.content.length > 500 ? msg.content.slice(0, 500) + "..." : msg.content;
    return (
      <div style={bubbleStyles.toolResult}>
        <button style={bubbleStyles.toolToggle} onClick={() => setExpanded(!expanded)}>
          <span style={bubbleStyles.toolIcon}>{expanded ? "\u25BC" : "\u25B6"}</span>
          <span style={bubbleStyles.toolResultLabel}>Result from {toolName}</span>
        </button>
        {expanded && (
          <pre style={bubbleStyles.toolArgs}>{content}</pre>
        )}
      </div>
    );
  }

  if (msg.type === "status") {
    return (
      <div style={bubbleStyles.status}>
        <span style={bubbleStyles.statusDot}>{"\u2022"}</span> {msg.content}
      </div>
    );
  }

  // Assistant message
  return (
    <div style={bubbleStyles.assistant}>
      {msg.content}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Component
// ---------------------------------------------------------------------------

export default function AgentDiscoveryView({ agentId, repoId, environment, onComplete, onCancel, onStateChange }: Props) {
  const {
    status,
    discoveryMode,
    activityLog,
    conversationMessages,
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

  const [startError, setStartError] = useState<string | null>(null);
  const conversationEndRef = useRef<HTMLDivElement>(null);

  // Start discovery on mount (only when agentId is valid)
  useEffect(() => {
    if (agentId && environment) {
      startDiscovery("", environment, undefined, agentId, repoId).catch((err: unknown) => {
        setStartError(err instanceof Error ? err.message : String(err));
      });
    }
  }, [agentId, repoId, environment, startDiscovery]);

  // Auto-scroll conversation to bottom
  useEffect(() => {
    if (conversationEndRef.current && typeof conversationEndRef.current.scrollIntoView === "function") {
      conversationEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [conversationMessages]);

  // Bubble up state changes to parent
  useEffect(() => {
    if (onStateChange) {
      onStateChange(discoveryState, conversationMessages);
    }
  }, [discoveryState, conversationMessages, onStateChange]);

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

      {/* Start error */}
      {startError && (
        <div role="alert" style={styles.errorBanner}>
          <strong>Failed to start discovery</strong>
          <pre style={{ margin: "8px 0 0", whiteSpace: "pre-wrap", fontSize: "0.8rem", color: "#ff6c8a" }}>{startError}</pre>
        </div>
      )}

      {/* Error */}
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

      {/* Main layout: conversation + plan panel */}
      <div style={styles.layout}>
        {/* Conversation Panel */}
        <div style={styles.conversationPanel}>
          <div style={styles.conversationHeader}>Agent Conversation</div>
          <div style={styles.conversationScroll}>
            {conversationMessages.length === 0 && (status === "connecting" || status === "discovering") && (
              <div style={styles.conversationEmpty}>
                {status === "connecting" ? "Connecting to agent..." : "Agent is analyzing your codebase..."}
              </div>
            )}
            {conversationMessages.map((msg) => (
              <ConversationBubble key={msg.id} msg={msg} />
            ))}
            <div ref={conversationEndRef} />
          </div>

          {/* Inline question */}
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
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const bubbleStyles: Record<string, React.CSSProperties> = {
  assistant: {
    padding: "10px 14px",
    backgroundColor: "#1a1a2e",
    borderRadius: 10,
    color: "#e0e0e8",
    fontSize: "0.85rem",
    lineHeight: 1.55,
    whiteSpace: "pre-wrap",
    wordBreak: "break-word",
    marginBottom: 8,
  },
  toolCall: {
    marginBottom: 6,
  },
  toolToggle: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    background: "none",
    border: "none",
    color: "#6c8aff",
    fontSize: "0.8rem",
    cursor: "pointer",
    padding: "4px 0",
  },
  toolIcon: {
    fontSize: "0.65rem",
  },
  toolLabel: {
    fontWeight: 500,
  },
  toolResultLabel: {
    fontWeight: 500,
    color: "#6cffa0",
  },
  toolArgs: {
    margin: "4px 0 8px 18px",
    padding: "8px 10px",
    backgroundColor: "#0a0a12",
    borderRadius: 6,
    color: "#a0a0b8",
    fontSize: "0.72rem",
    lineHeight: 1.5,
    overflow: "auto",
    maxHeight: 200,
    whiteSpace: "pre-wrap",
    wordBreak: "break-all",
  },
  status: {
    padding: "4px 8px",
    color: "#8888a0",
    fontSize: "0.78rem",
    marginBottom: 4,
  },
  statusDot: {
    color: "#6c8aff",
  },
};

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
  conversationPanel: {
    flex: "1 1 400px",
    minWidth: 300,
    display: "flex",
    flexDirection: "column",
    backgroundColor: "#12121a",
    borderRadius: 10,
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#1e1e2e",
    overflow: "hidden",
  },
  conversationHeader: {
    padding: "10px 14px",
    fontSize: "0.85rem",
    fontWeight: 600,
    color: "#8888a0",
    borderBottomWidth: 1,
    borderBottomStyle: "solid",
    borderBottomColor: "#1e1e2e",
  },
  conversationScroll: {
    flex: 1,
    padding: "12px 14px",
    overflow: "auto",
    maxHeight: 500,
    minHeight: 200,
  },
  conversationEmpty: {
    color: "#5a5a70",
    fontSize: "0.85rem",
    textAlign: "center",
    padding: "40px 20px",
  },
  questionArea: { marginTop: 4, padding: "0 14px 12px" },
  planPanel: {
    flex: "1 1 350px",
    minWidth: 300,
  },
};
