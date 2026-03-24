import React from "react";
import type { PlanCardData } from "@/lib/types";
import { statusEmoji } from "@/lib/theme";

interface PlanCardProps {
  plan: PlanCardData;
  onViewBoard?: () => void;
}

export default function PlanCard({ plan, onViewBoard }: PlanCardProps) {
  const headerEmoji = plan.status === "active" ? "\uD83D\uDD04"
    : plan.status === "completed" ? "\u2705"
    : plan.status === "failed" ? "\u274C"
    : "\uD83D\uDCCB";

  return (
    <div style={styles.planCard}>
      <div style={styles.planCardHeader}>
        <span>
          {headerEmoji}{" "}
          Plan: {plan.title}
        </span>
        {onViewBoard ? (
          <button onClick={onViewBoard} style={styles.planCardViewBtn}>View</button>
        ) : (
          <a href={`/board?plan=${plan.id}`} style={styles.planCardViewBtn}>View</a>
        )}
      </div>
      {plan.items.length > 0 && (
        <div style={styles.planCardItems}>
          {plan.items.map(item => (
            <div key={item.id} style={styles.planCardItem}>
              <span>{statusEmoji(item.status)}</span>
              <span style={{ color: item.status === "in_progress" ? "#6c8aff" : "#e0e0e8" }}>{item.title}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  planCard: {
    padding: "12px 16px",
    borderRadius: "12px",
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    maxWidth: "85%",
    alignSelf: "center" as const,
  },
  planCardHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    fontSize: "0.85rem",
    fontWeight: 600,
    color: "#e0e0e8",
    marginBottom: "8px",
  },
  planCardViewBtn: {
    color: "#6c8aff",
    textDecoration: "none",
    fontSize: "0.78rem",
    padding: "4px 10px",
    borderRadius: "6px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    background: "none",
    cursor: "pointer",
  },
  planCardItems: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "4px",
  },
  planCardItem: {
    display: "flex",
    gap: "8px",
    alignItems: "center",
    fontSize: "0.82rem",
    padding: "2px 0",
  },
};
