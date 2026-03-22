import React, { useRef, useEffect, useState } from "react";
import type { WorkPlan } from "@/lib/types";
import { STATUS_EMOJI } from "@/lib/theme";

interface PlanSelectorProps {
  plans: WorkPlan[];
  selectedPlanId: string | null;
  selectedPlan: WorkPlan | null;
  onSelect: (planId: string) => void;
}

function formatAge(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
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
          {plans.map(p => {
            const itemCount = p.items?.length || 0;
            const doneCount = p.items?.filter(i => i.status === "done" || i.status === "complete").length || 0;
            const age = formatAge(p.created_at);
            return (
              <div
                key={p.id}
                onClick={() => { onSelect(p.id); setOpen(false); }}
                style={{
                  padding: "10px 14px",
                  cursor: "pointer",
                  fontSize: "0.85rem",
                  color: p.id === selectedPlanId ? "#6c8aff" : "#e0e0e8",
                  backgroundColor: p.id === selectedPlanId ? "#12121a" : "transparent",
                  borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1a1a2a",
                }}
                onMouseEnter={e => { if (p.id !== selectedPlanId) (e.currentTarget as HTMLElement).style.backgroundColor = "#2a2a3e"; }}
                onMouseLeave={e => { if (p.id !== selectedPlanId) (e.currentTarget as HTMLElement).style.backgroundColor = "transparent"; }}
              >
                <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                  <span>{STATUS_EMOJI[p.status] || "\uD83D\uDCCB"}</span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", fontWeight: 500 }}>{p.title}</span>
                </div>
                <div style={{ fontSize: "0.75rem", color: "#5a5a6e", marginTop: "4px", paddingLeft: "26px", display: "flex", gap: "8px" }}>
                  {itemCount > 0 && <span>{doneCount}/{itemCount} items</span>}
                  <span>{age}</span>
                  {p.agent_id && <span style={{ color: "#6c8aff" }}>{p.agent_id.slice(-6)}</span>}
                </div>
                {p.items && p.items.length > 0 && (
                  <div style={{ fontSize: "0.72rem", color: "#4a4a5e", marginTop: "3px", paddingLeft: "26px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {p.items.slice(0, 3).map(i => `${i.status === "done" ? "✓" : "○"} ${i.title}`).join("  ·  ")}
                    {p.items.length > 3 && `  (+${p.items.length - 3} more)`}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  planSelectorBtn: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "10px",
    zIndex: 100,
    boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
  },
};
