"use client";

import React from "react";

export interface AppInfo {
  id: string;
  name: string;
  displayName: string;
  componentType: string;
  framework: string;
  runtime: string;
  repositoryUrl: string;
  environments: { name: string; status: "healthy" | "warning" | "error" | "inactive" }[];
  healthStatus: "healthy" | "degraded" | "down" | "unknown";
  alertCount: number;
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

const TYPE_COLORS: Record<string, string> = {
  service: "#6c8aff",
  frontend: "#ff6cc9",
  database: "#ffcc6c",
  system: "#5a5a70",
};

export default function AppCard({ app, onClick }: Props) {
  return (
    <div style={s.card} onClick={onClick} role="button" tabIndex={0} onKeyDown={(e) => e.key === "Enter" && onClick()}>
      <div style={s.topRow}>
        <div style={s.name}>{app.displayName || app.name}</div>
        <span style={{ ...s.typeBadge, backgroundColor: TYPE_COLORS[app.componentType] || "#5a5a70" }}>
          {app.componentType}
        </span>
      </div>
      <div style={s.framework}>
        {app.framework}{app.runtime ? ` / ${app.runtime}` : ""}
      </div>
      {app.repositoryUrl && (
        <div style={s.repo}>{app.repositoryUrl.replace(/^https?:\/\//, "").slice(0, 40)}</div>
      )}
      <div style={s.envRow}>
        {app.environments.map((env) => (
          <span key={env.name} style={s.envPill}>
            <span style={{ ...s.envDot, backgroundColor: STATUS_COLORS[env.status] || "#5a5a70" }} />
            {env.name}
          </span>
        ))}
      </div>
      <div style={s.meta}>
        {app.alertCount > 0 && <span style={s.alertBadge}>{app.alertCount} alert{app.alertCount > 1 ? "s" : ""}</span>}
        <span style={{ color: STATUS_COLORS[app.healthStatus] || "#5a5a70", marginLeft: "auto" }}>
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
  topRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  name: { fontSize: "1.05rem", fontWeight: 600, color: "#e0e0e8" },
  typeBadge: {
    fontSize: "0.7rem", fontWeight: 600, color: "#fff", padding: "2px 8px",
    borderRadius: "10px", textTransform: "uppercase", letterSpacing: "0.5px",
  },
  framework: { fontSize: "0.8rem", color: "#8888a0" },
  repo: { fontSize: "0.75rem", color: "#5a5a70", fontFamily: "monospace" },
  envRow: { display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "4px" },
  envPill: {
    display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "0.75rem", color: "#8888a0",
    padding: "3px 8px", borderRadius: "12px", backgroundColor: "#1e1e2e",
  },
  envDot: { width: "6px", height: "6px", borderRadius: "50%", display: "inline-block", flexShrink: 0 },
  meta: { display: "flex", alignItems: "center", fontSize: "0.78rem", color: "#5a5a70", marginTop: "4px" },
  alertBadge: { color: "#ff6c8a", fontWeight: 500 },
};
