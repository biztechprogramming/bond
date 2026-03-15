import React from "react";

const TYPE_ICONS: Record<string, string> = {
  "local": "💻",
  "linux-server": "🖥",
  "kubernetes": "☸",
  "docker-host": "🐳",
  "aws-ecs": "☁",
  "custom": "⚙",
};

interface Props {
  resource: any;
  onClick: () => void;
}

export default function ResourceCard({ resource, onClick }: Props) {
  const state = typeof resource.state_json === "string"
    ? JSON.parse(resource.state_json || "{}") : (resource.state_json || {});
  const status = state.status || "unknown";
  const statusColor = status === "online" ? "#6cffa0" : status === "pending" ? "#ffcc6c" : "#8888a0";
  const icon = TYPE_ICONS[resource.resource_type] || "📦";

  return (
    <div style={styles.card} onClick={onClick}>
      <div style={styles.topRow}>
        <span style={{ fontSize: "1.4rem" }}>{icon}</span>
        <div style={styles.info}>
          <span style={styles.name}>{resource.display_name || resource.name}</span>
          <span style={styles.type}>{resource.resource_type} &middot; {resource.environment}</span>
        </div>
        <span style={{ ...styles.dot, backgroundColor: statusColor }} title={status} />
      </div>
      {(state.os || state.cpus || state.memory_gb) && (
        <div style={styles.stats}>
          {state.os && <span style={styles.stat}>{state.os}</span>}
          {state.cpus && <span style={styles.stat}>{state.cpus} CPU</span>}
          {state.memory_gb !== undefined && <span style={styles.stat}>{state.memory_gb}GB RAM</span>}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  card: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: 12,
    padding: 14,
    cursor: "pointer",
    display: "flex",
    flexDirection: "column",
    gap: 8,
    transition: "border-color 0.15s",
  },
  topRow: { display: "flex", alignItems: "center", gap: 10 },
  info: { display: "flex", flexDirection: "column", flex: 1, gap: 2 },
  name: { fontSize: "0.9rem", fontWeight: 600, color: "#e0e0e8" },
  type: { fontSize: "0.75rem", color: "#8888a0" },
  dot: { width: 10, height: 10, borderRadius: "50%", flexShrink: 0 },
  stats: { display: "flex", gap: 10, flexWrap: "wrap" },
  stat: { fontSize: "0.75rem", color: "#8888a0", backgroundColor: "#0a0a12", padding: "2px 8px", borderRadius: 4 },
};
