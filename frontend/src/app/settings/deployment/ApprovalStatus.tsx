import React, { useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Approval {
  user_id: string;
  approved_at: string;
}

interface Props {
  scriptId: string;
  version: string;
  environment: string;
  requiredApprovals: number;
  approvals: Approval[];
  onApprove?: () => void;
}

export default function ApprovalStatus({
  scriptId,
  version,
  environment,
  requiredApprovals,
  approvals,
  onApprove,
}: Props) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleApprove = async () => {
    setSubmitting(true);
    setError("");
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/promote/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ script_id: scriptId, version, environment }),
      });
      if (res.ok) {
        onApprove?.();
      } else {
        const data = await res.json();
        setError(data.error || "Approval failed");
      }
    } catch {
      setError("Network error");
    }
    setSubmitting(false);
  };

  const approvedCount = approvals.length;

  return (
    <span style={styles.container}>
      <span style={styles.count}>
        {approvedCount}/{requiredApprovals} approvals
      </span>
      {approvals.map((a) => (
        <span key={a.user_id} style={styles.approved}>
          {a.user_id}
        </span>
      ))}
      <button
        style={styles.approveBtn}
        onClick={handleApprove}
        disabled={submitting}
      >
        {submitting ? "..." : "Approve"}
      </button>
      {error && <span style={styles.error}>{error}</span>}
    </span>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" as const },
  count: { fontSize: "0.75rem", color: "#ffcc44" },
  approved: { fontSize: "0.7rem", color: "#6cffa0" },
  approveBtn: {
    backgroundColor: "#2a4a2a",
    color: "#6cffa0",
    border: "1px solid #3a5a3a",
    borderRadius: 4,
    padding: "2px 8px",
    fontSize: "0.7rem",
    cursor: "pointer",
  },
  error: { fontSize: "0.7rem", color: "#ff6c8a" },
};
