"use client";

import React, { useEffect, useState } from "react";
import OverviewPanel from "./OverviewPanel";
import LessonsPanel from "./LessonsPanel";
import ParametersPanel from "./ParametersPanel";
import ExperimentsPanel from "./ExperimentsPanel";
import RetentionPanel from "./RetentionPanel";

const SUB_TABS = [
  { id: "overview", label: "Overview" },
  { id: "lessons", label: "Lessons" },
  { id: "parameters", label: "Parameters" },
  { id: "experiments", label: "Experiments" },
  { id: "retention", label: "Retention" },
] as const;

type SubTabId = (typeof SUB_TABS)[number]["id"];

export default function OptimizationTab() {
  const [activeSubTab, setActiveSubTab] = useState<SubTabId>("overview");
  const [isMobile, setIsMobile] = useState(false);

  // Read hash on mount
  useEffect(() => {
    const hash = window.location.hash;
    const match = hash.match(/^#optimization\/(\w+)$/);
    if (match && SUB_TABS.some((t) => t.id === match[1])) {
      setActiveSubTab(match[1] as SubTabId);
    }
  }, []);

  // Responsive check
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < 768);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);

  const switchSubTab = (id: SubTabId) => {
    setActiveSubTab(id);
    window.history.replaceState(null, "", `#optimization/${id}`);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Sub-tab navigation */}
      {isMobile ? (
        <select
          value={activeSubTab}
          onChange={(e) => switchSubTab(e.target.value as SubTabId)}
          style={{
            backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: "8px",
            padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none",
          }}
          aria-label="Optimization sub-tab"
        >
          {SUB_TABS.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
        </select>
      ) : (
        <div role="tablist" aria-label="Optimization sections" style={{ display: "flex", gap: "4px" }}>
          {SUB_TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={activeSubTab === t.id}
              aria-controls={`tabpanel-${t.id}`}
              onClick={() => switchSubTab(t.id)}
              style={{
                background: activeSubTab === t.id ? "#1a1a2e" : "none",
                borderWidth: "1px",
                borderStyle: "solid",
                borderColor: activeSubTab === t.id ? "#6c8aff" : "#2a2a3e",
                borderRadius: "8px",
                color: activeSubTab === t.id ? "#6c8aff" : "#8888a0",
                padding: "8px 16px",
                fontSize: "0.85rem",
                cursor: "pointer",
                fontWeight: activeSubTab === t.id ? 600 : 400,
                outline: "none",
              }}
              onFocus={(e) => { e.currentTarget.style.boxShadow = "0 0 0 2px #6c8aff"; }}
              onBlur={(e) => { e.currentTarget.style.boxShadow = "none"; }}
            >
              {t.label}
            </button>
          ))}
        </div>
      )}

      {/* Tab panels */}
      <div role="tabpanel" id={`tabpanel-${activeSubTab}`} aria-label={`${activeSubTab} panel`}>
        {activeSubTab === "overview" && <OverviewPanel />}
        {activeSubTab === "lessons" && <LessonsPanel />}
        {activeSubTab === "parameters" && <ParametersPanel />}
        {activeSubTab === "experiments" && <ExperimentsPanel />}
        {activeSubTab === "retention" && <RetentionPanel />}
      </div>
    </div>
  );
}
