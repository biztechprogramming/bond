import React from "react";

export type DeployStatus = "success" | "deploying" | "failed" | "not_promoted" | "pending" | "unknown";

const STATUS_MAP: Record<DeployStatus, { icon: string; color: string; label: string }> = {
  success: { icon: "\u2705", color: "#6cffa0", label: "Healthy" },
  deploying: { icon: "\u23f3", color: "#ffcc44", label: "Deploying" },
  failed: { icon: "\u274c", color: "#ff6c8a", label: "Failed" },
  not_promoted: { icon: "\u25cb", color: "#8888a0", label: "Not Promoted" },
  pending: { icon: "\u25cb", color: "#8888a0", label: "Pending" },
  unknown: { icon: "\u25cb", color: "#5a5a6e", label: "Unknown" },
};

interface Props {
  status: DeployStatus;
  showLabel?: boolean;
}

export default function StatusIndicator({ status, showLabel = true }: Props) {
  const s = STATUS_MAP[status] || STATUS_MAP.unknown;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "0.8rem", color: s.color }}>
      <span>{s.icon}</span>
      {showLabel && <span>{s.label}</span>}
    </span>
  );
}
