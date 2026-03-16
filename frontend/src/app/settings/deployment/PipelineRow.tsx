import React, { useState } from "react";
import { GATEWAY_API } from "@/lib/config";
import StatusIndicator, { DeployStatus } from "./StatusIndicator";
import ApprovalStatus from "./ApprovalStatus";

interface EnvStatus {
  environment: string;
  status: DeployStatus;
  promotion_id?: string;
}

interface Props {
  scriptName: string;
  version: string;
  environments: EnvStatus[];
  onRefresh?: () => void;
  onStatusClick?: (environment: string, receiptId?: string) => void;
}

export default function PipelineRow({ scriptName, version, environments, onRefresh, onStatusClick }: Props) {
  const [promoting, setPromoting] = useState<string | null>(null);
  const [approvalInfo, setApprovalInfo] = useState<Record<string, any>>({});

  const handlePromote = async (targetEnv: string) => {
    setPromoting(targetEnv);
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script_id: scriptName,
          version,
          target_environments: [targetEnv],
        }),
      });
      const result = await res.json();
      if (result.status === "promoted") {
        onRefresh?.();
      } else if (result.status === "awaiting_approvals") {
        setApprovalInfo((prev) => ({ ...prev, [targetEnv]: result }));
      }
    } catch { /* ignore */ }
    setPromoting(null);
  };

  const handlePromoteAll = async () => {
    const remaining = environments
      .filter((e) => e.status === "not_promoted" || e.status === "pending")
      .map((e) => e.environment);
    if (remaining.length === 0) return;

    setPromoting("all");
    try {
      await fetch(`${GATEWAY_API}/deployments/promote`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script_id: scriptName,
          version,
          target_environments: remaining,
        }),
      });
      onRefresh?.();
    } catch { /* ignore */ }
    setPromoting(null);
  };

  const canPromote = (env: EnvStatus, idx: number): boolean => {
    if (env.status !== "not_promoted" && env.status !== "pending") return false;
    if (idx === 0) return true;
    const prev = environments[idx - 1];
    return prev?.status === "success" || prev?.status === "deploying";
  };

  const hasRemaining = environments.some(
    (e) => e.status === "not_promoted" || e.status === "pending"
  );

  return (
    <div style={styles.row}>
      <div style={styles.nameRow}>
        <div style={styles.name}>
          {scriptName} <span style={styles.version}>({version})</span>
        </div>
        {hasRemaining && (
          <button
            style={styles.promoteAllBtn}
            onClick={handlePromoteAll}
            disabled={promoting === "all"}
          >
            {promoting === "all" ? "Promoting..." : "Promote to All"}
          </button>
        )}
      </div>
      <div style={styles.envLine}>
        {environments.map((env, i) => (
          <React.Fragment key={env.environment}>
            <span
              style={{ cursor: onStatusClick ? "pointer" : "default" }}
              onClick={() => onStatusClick?.(env.environment)}
            >
              <StatusIndicator status={env.status} showLabel={false} />
            </span>
            <span style={styles.envLabel}>{env.environment}</span>
            {canPromote(env, i) && (
              <button
                style={styles.promoteBtn}
                onClick={() => handlePromote(env.environment)}
                disabled={promoting === env.environment}
              >
                {promoting === env.environment ? "..." : "Promote"}
              </button>
            )}
            {approvalInfo[env.environment]?.status === "awaiting_approvals" && (
              <span style={styles.approvalBadge}>
                {approvalInfo[env.environment].approvals.received}/{approvalInfo[env.environment].approvals.required} approvals
              </span>
            )}
            {i < environments.length - 1 && <span style={styles.arrow}>&rarr;</span>}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  row: { padding: "12px 0", borderBottom: "1px solid #1e1e2e" },
  nameRow: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 6 },
  name: { fontSize: "0.9rem", color: "#e0e0e8", fontWeight: 500 },
  version: { fontSize: "0.8rem", color: "#8888a0", fontWeight: 400 },
  envLine: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" as const },
  envLabel: { fontSize: "0.75rem", color: "#8888a0", textTransform: "uppercase" as const },
  arrow: { color: "#5a5a6e", fontSize: "0.85rem" },
  promoteBtn: {
    backgroundColor: "#2a4a2a",
    color: "#6cffa0",
    border: "1px solid #3a5a3a",
    borderRadius: 6,
    padding: "2px 10px",
    fontSize: "0.7rem",
    cursor: "pointer",
    fontWeight: 600,
  },
  promoteAllBtn: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: 6,
    padding: "4px 12px",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  approvalBadge: {
    fontSize: "0.7rem",
    color: "#ffcc44",
    backgroundColor: "#2a2a1a",
    padding: "2px 6px",
    borderRadius: 4,
  },
};
