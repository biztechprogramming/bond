import React from "react";

interface Agent {
  id: string;
  name: string;
  display_name: string;
  model: string;
  utility_model: string;
  is_active: boolean;
}

interface Props {
  agent: Agent;
  envLabel: string;
  sharedModel: string;
  sharedUtilityModel: string;
  onClick: () => void;
}

export default function AgentCard({ agent, envLabel, sharedModel, sharedUtilityModel, onClick }: Props) {
  const hasModelOverride = agent.model !== sharedModel;
  const hasUtilityOverride = agent.utility_model !== sharedUtilityModel;
  const hasOverride = hasModelOverride || hasUtilityOverride;

  return (
    <div style={styles.card} onClick={onClick}>
      <div style={styles.envRow}>
        <span style={styles.envDot}>{agent.is_active ? "\u25cf" : "\u25cb"}</span>
        <span style={styles.envLabel}>{envLabel.toUpperCase()}</span>
        {hasOverride && <span style={styles.gearBadge} title="Has model overrides">{"\u2699"}</span>}
      </div>
      <div style={styles.displayName}>{agent.display_name || agent.name}</div>
      <div style={styles.model}>{agent.model.split("/").pop()}</div>
      <button
        style={styles.editBtn}
        onClick={(e) => { e.stopPropagation(); onClick(); }}
      >
        Edit
      </button>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "16px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    cursor: "pointer",
    transition: "border-color 0.2s",
    display: "flex",
    flexDirection: "column",
    gap: "6px",
    minWidth: "140px",
  },
  envRow: { display: "flex", alignItems: "center", gap: "6px" },
  envDot: { color: "#6cffa0", fontSize: "0.7rem" },
  envLabel: { fontSize: "0.75rem", fontWeight: 700, color: "#8888a0", textTransform: "uppercase" as const, letterSpacing: "0.05em" },
  gearBadge: { fontSize: "0.85rem", color: "#ffcc44", marginLeft: "auto" },
  displayName: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  model: { fontSize: "0.8rem", color: "#8888a0" },
  editBtn: {
    marginTop: "8px",
    backgroundColor: "#2a2a3e",
    color: "#6c8aff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "6px",
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
    alignSelf: "flex-start",
  },
};
