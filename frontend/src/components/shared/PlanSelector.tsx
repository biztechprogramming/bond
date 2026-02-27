import React, { useRef, useEffect, useState } from "react";
import type { WorkPlan } from "@/lib/types";
import { STATUS_EMOJI } from "@/lib/theme";

interface PlanSelectorProps {
  plans: WorkPlan[];
  selectedPlanId: string | null;
  selectedPlan: WorkPlan | null;
  onSelect: (planId: string) => void;
}

export default function PlanSelector({ plans, selectedPlanId, selectedPlan, onSelect }: PlanSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div className="board-plan-selector" ref={ref} style={{ position: "relative", flex: 1, maxWidth: "500px", margin: "0 16px" }}>
      <button
        onClick={() => setOpen(!open)}
        style={styles.planSelectorBtn}
      >
        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
          {selectedPlan ? `${STATUS_EMOJI[selectedPlan.status] || ""} ${selectedPlan.title}` : "Select a plan..."}
        </span>
        <span style={{ fontSize: "0.7rem", color: "#5a5a6e", flexShrink: 0 }}>{open ? "\u25B2" : "\u25BC"}</span>
      </button>
      {open && (
        <div style={styles.planDropdown}>
          {plans.length === 0 && (
            <div style={{ padding: "12px 14px", color: "#5a5a6e", fontSize: "0.85rem" }}>
              No plans yet
            </div>
          )}
          {plans.map(p => (
            <div
              key={p.id}
              onClick={() => { onSelect(p.id); setOpen(false); }}
              style={{
                padding: "10px 14px",
                cursor: "pointer",
                fontSize: "0.85rem",
                color: p.id === selectedPlanId ? "#6c8aff" : "#e0e0e8",
                backgroundColor: p.id === selectedPlanId ? "#12121a" : "transparent",
                display: "flex",
                gap: "8px",
                alignItems: "center",
              }}
              onMouseEnter={e => { if (p.id !== selectedPlanId) (e.currentTarget as HTMLElement).style.backgroundColor = "#2a2a3e"; }}
              onMouseLeave={e => { if (p.id !== selectedPlanId) (e.currentTarget as HTMLElement).style.backgroundColor = "transparent"; }}
            >
              <span>{STATUS_EMOJI[p.status] || "\uD83D\uDCCB"}</span>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.title}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  planSelectorBtn: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "8px 14px",
    color: "#e0e0e8",
    fontSize: "0.9rem",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "8px",
    textAlign: "left" as const,
  },
  planDropdown: {
    position: "absolute" as const,
    top: "calc(100% + 4px)",
    left: 0,
    right: 0,
    maxHeight: "300px",
    overflowY: "auto" as const,
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "10px",
    zIndex: 100,
    boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
  },
};
