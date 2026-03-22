"use client";

import React from "react";

export interface AppInfo {
  id: string;
  name: string;
  framework: string;
  environments: { name: string; status: "healthy" | "warning" | "error" | "inactive" }[];
  lastDeployTime: string | null;
  healthStatus: "healthy" | "degraded" | "down" | "unknown";
}

interface Props {
  app: AppInfo;
  onClick: () => void;
}

const STATUS_COLORS: Record<string, string> = {
  healthy: "#6cffa0",
  warning: "#ffcc6c",
  error: "#ff6c8a",
  inactive: "#5a5a70",
  degraded: "#ffcc6c",
  down: "#ff6c8a",
  unknown: "#5a5a70",
};

export default function AppCard({ app, onClick }: Props) {
  return (
    <div style={s.card} onClick={onClick} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onClick()}>
      <div style={s.name}>{app.name}</div>
      <div style={s.framework}>{app.framework}</div>
      <div style={s.envRow}>
        {app.environments.map((env) => (
          <span key={env.name} style={s.envPill}>
            <span style={{ ...s.envDot, backgroundColor: STATUS_COLORS[env.status] || "#5a5a70" }} />
            {env.name}
          </span>
        ))}
      </div>
      <div style={s.meta}>
        {app.lastDeployTime && <span>Last deploy: {app.lastDeployTime}</span>}
        <span style={{ color: STATUS_COLORS[app.healthStatus] || "#5a5a70" }}>
          {app.healthStatus === "healthy" ? "Healthy" : app.healthStatus === "degraded" ? "Degraded" : app.healthStatus === "down" ? "Down" : "Unknown"}
        </span>
      </div>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  card: {
    backgroundColor: "#12121a", borderRadius: "12px", padding: "20px", border: "1px solid #1e1e2e",
    cursor: "pointer", transition: "border-color 0.2s, transform 0.15s", display: "flex", flexDirection: "column", gap: "8px",
  },
  name: { fontSize: "1.05rem", fontWeight: 600, color: "#e0e0e8" },
  framework: { fontSize: "0.8rem", color: "#8888a0" },
  envRow: { display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "4px" },
  envPill: {
    display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "0.75rem", color: "#8888a0",
    padding: "3px 8px", borderRadius: "12px", backgroundColor: "#1e1e2e",
  },
  envDot: { width: "6px", height: "6px", borderRadius: "50%", display: "inline-block", flexShrink: 0 },
  meta: { display: "flex", justifyContent: "space-between", fontSize: "0.78rem", color: "#5a5a70", marginTop: "4px" },
};
