import React, { useState, useEffect } from "react";

export interface SkillActivation {
  id: string;           // unique ID for this activation
  skillName: string;    // e.g. "claude-api"
  skillSource: string;  // e.g. "anthropics" or "local"
  activatedAt: number;  // timestamp
}

interface SkillFeedbackToastProps {
  activation: SkillActivation;
  onFeedback: (activationId: string, vote: "up" | "down") => void;
  onDismiss: (activationId: string) => void;
}

/**
 * Persistent toast that appears when a skill is activated.
 * Does NOT auto-dismiss — stays until the user votes or explicitly closes.
 */
function SkillFeedbackToast({ activation, onFeedback, onDismiss }: SkillFeedbackToastProps) {
  const [voted, setVoted] = useState<"up" | "down" | null>(null);
  const [fadeOut, setFadeOut] = useState(false);

  // After voting, fade out after 1.5s
  useEffect(() => {
    if (voted) {
      const timer = setTimeout(() => setFadeOut(true), 1500);
      const removeTimer = setTimeout(() => onDismiss(activation.id), 1800);
      return () => { clearTimeout(timer); clearTimeout(removeTimer); };
    }
  }, [voted, activation.id, onDismiss]);

  const handleVote = (vote: "up" | "down") => {
    setVoted(vote);
    onFeedback(activation.id, vote);
  };

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 12,
        padding: "10px 14px",
        backgroundColor: "rgba(30, 30, 45, 0.95)",
        borderWidth: "1px", borderStyle: "solid", borderColor: "rgba(100, 100, 140, 0.3)",
        borderRadius: 10,
        backdropFilter: "blur(12px)",
        boxShadow: "0 4px 20px rgba(0, 0, 0, 0.4)",
        opacity: fadeOut ? 0 : 1,
        transform: fadeOut ? "translateX(40px)" : "translateX(0)",
        transition: "opacity 0.3s ease, transform 0.3s ease",
        maxWidth: 360,
        fontSize: 13,
        color: "#e0e0e8",
        lineHeight: 1.4,
      }}
    >
      {/* Skill icon */}
      <span style={{ fontSize: 18, flexShrink: 0 }}>🧩</span>

      {/* Info */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontWeight: 600, color: "#c8c8e0" }}>
          {activation.skillName}
        </div>
        <div style={{ fontSize: 11, color: "#888", marginTop: 2 }}>
          {voted
            ? voted === "up" ? "Thanks! Marked as helpful." : "Got it — we'll improve matching."
            : `from ${activation.skillSource} • Was this helpful?`
          }
        </div>
      </div>

      {/* Vote buttons — hidden after voting */}
      {!voted && (
        <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
          <button
            onClick={() => handleVote("up")}
            title="Skill was helpful"
            style={{
              background: "none",
              borderWidth: "1px", borderStyle: "solid", borderColor: "rgba(100, 200, 100, 0.3)",
              borderRadius: 6,
              padding: "4px 8px",
              cursor: "pointer",
              fontSize: 16,
              lineHeight: 1,
              transition: "background 0.15s, border-color 0.15s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "rgba(100, 200, 100, 0.15)";
              e.currentTarget.style.borderColor = "rgba(100, 200, 100, 0.5)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "none";
              e.currentTarget.style.borderColor = "rgba(100, 200, 100, 0.3)";
            }}
          >
            👍
          </button>
          <button
            onClick={() => handleVote("down")}
            title="Skill wasn't relevant"
            style={{
              background: "none",
              borderWidth: "1px", borderStyle: "solid", borderColor: "rgba(200, 100, 100, 0.3)",
              borderRadius: 6,
              padding: "4px 8px",
              cursor: "pointer",
              fontSize: 16,
              lineHeight: 1,
              transition: "background 0.15s, border-color 0.15s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "rgba(200, 100, 100, 0.15)";
              e.currentTarget.style.borderColor = "rgba(200, 100, 100, 0.5)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "none";
              e.currentTarget.style.borderColor = "rgba(200, 100, 100, 0.3)";
            }}
          >
            👎
          </button>
          {/* Dismiss X */}
          <button
            onClick={() => onDismiss(activation.id)}
            title="Dismiss"
            style={{
              background: "none",
              borderWidth: 0, borderStyle: "none", borderColor: "transparent",
              padding: "4px 4px",
              cursor: "pointer",
              fontSize: 12,
              color: "#666",
              lineHeight: 1,
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "#aaa"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "#666"; }}
          >
            ✕
          </button>
        </div>
      )}

      {/* Voted indicator */}
      {voted && (
        <span style={{ fontSize: 20, flexShrink: 0 }}>
          {voted === "up" ? "👍" : "👎"}
        </span>
      )}
    </div>
  );
}

/**
 * Container that stacks multiple skill feedback toasts.
 * Renders in the bottom-right corner, fixed position.
 */
export interface SkillFeedbackStackProps {
  activations: SkillActivation[];
  onFeedback: (activationId: string, vote: "up" | "down") => void;
  onDismiss: (activationId: string) => void;
}

export default function SkillFeedbackStack({
  activations,
  onFeedback,
  onDismiss,
}: SkillFeedbackStackProps) {
  if (activations.length === 0) return null;

  return (
    <div
      style={{
        position: "fixed",
        bottom: 20,
        right: 20,
        display: "flex",
        flexDirection: "column",
        gap: 8,
        zIndex: 9999,
        pointerEvents: "auto",
      }}
    >
      {activations.map((activation) => (
        <SkillFeedbackToast
          key={activation.id}
          activation={activation}
          onFeedback={onFeedback}
          onDismiss={onDismiss}
        />
      ))}
    </div>
  );
}
