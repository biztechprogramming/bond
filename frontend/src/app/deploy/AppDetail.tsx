"use client";

import React, { useState, useMemo } from "react";
import { useAgentsWithRelations, useComponents } from "@/hooks/useSpacetimeDB";
import PipelineSection from "../settings/deployment/PipelineSection";
import MonitoringSection from "../settings/deployment/MonitoringSection";
import DeploymentTimeline from "../settings/deployment/DeploymentTimeline";
import LiveLogViewer from "../settings/deployment/LiveLogViewer";
import SecretManager from "../settings/deployment/SecretManager";
import AlertRulesEditor from "../settings/deployment/AlertRulesEditor";
import PipelineYamlEditor from "../settings/deployment/PipelineYamlEditor";
import ScriptRegistration from "../settings/deployment/ScriptRegistration";

type Tab = "overview" | "pipelines" | "monitoring" | "timeline" | "logs";

interface Props {
  appId: string;
  onBack: () => void;
}

export default function AppDetail({ appId, onBack }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [showAdvanced, setShowAdvanced] = useState(false);

  const agents = useAgentsWithRelations();
  const components = useComponents();

  // Find agents and components belonging to this app
  const appAgents = useMemo(
    () => agents.filter((a) => a.name.startsWith(`deploy-${appId}`)),
    [agents, appId]
  );

  const appComponents = useMemo(
    () => components.filter((c) => c.name.includes(appId)),
    [components, appId]
  );

  const appName = appAgents[0]?.display_name || appId;
  const primaryEnv = appAgents[0]?.name.split("-").pop() || "dev";

  const tabs: { id: Tab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "pipelines", label: "Pipelines" },
    { id: "monitoring", label: "Monitoring" },
    { id: "timeline", label: "Timeline" },
    { id: "logs", label: "Logs" },
  ];

  return (
    <div>
      {/* Header */}
      <div style={s.header}>
        <button style={s.backBtn} onClick={onBack}>&larr; Back</button>
        <h2 style={s.appName}>{appName}</h2>
        <div style={s.envBadges}>
          {appAgents.map((a) => {
            const env = a.name.split("-").pop() || "?";
            return (
              <span key={a.id} style={{ ...s.envBadge, borderColor: a.is_active ? "#6cffa0" : "#5a5a70" }}>
                <span style={{ ...s.dot, backgroundColor: a.is_active ? "#6cffa0" : "#5a5a70" }} />
                {env}
              </span>
            );
          })}
        </div>
      </div>

      {/* Tab bar */}
      <div style={s.tabBar}>
        {tabs.map((t) => (
          <button
            key={t.id}
            style={tab === t.id ? { ...s.tab, ...s.tabActive } : s.tab}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={s.content}>
        {tab === "overview" && (
          <div style={s.section}>
            <h3 style={s.sectionTitle}>Application Overview</h3>
            <div style={s.details}>
              <div style={s.detailRow}><span style={s.detailLabel}>App ID</span><span>{appId}</span></div>
              <div style={s.detailRow}><span style={s.detailLabel}>Environments</span><span>{appAgents.length}</span></div>
              <div style={s.detailRow}><span style={s.detailLabel}>Components</span><span>{appComponents.length}</span></div>
            </div>
            {/* Quick actions */}
            <div style={{ display: "flex", gap: "8px", marginTop: "16px" }}>
              <button style={s.actionBtn} onClick={() => setTab("pipelines")}>View Pipelines</button>
              <button style={s.actionBtn} onClick={() => setTab("logs")}>View Logs</button>
              <button style={{ ...s.actionBtn, ...s.advancedToggle }} onClick={() => setShowAdvanced(!showAdvanced)}>
                {showAdvanced ? "Hide" : "Show"} Advanced
              </button>
            </div>
          </div>
        )}

        {tab === "pipelines" && (
          <PipelineSection environmentNames={[primaryEnv]} />
        )}

        {tab === "monitoring" && (
          <MonitoringSection environment={primaryEnv} />
        )}

        {tab === "timeline" && (
          <DeploymentTimeline environments={[{ name: primaryEnv, display_name: primaryEnv }]} />
        )}

        {tab === "logs" && (
          <LiveLogViewer environment={primaryEnv} />
        )}
      </div>

      {/* Advanced drawer */}
      {showAdvanced && (
        <div style={s.advancedDrawer}>
          <h3 style={s.sectionTitle}>Advanced Settings</h3>
          <div style={s.advancedGrid}>
            <div style={s.advancedCard}>
              <h4 style={s.advancedCardTitle}>Secrets</h4>
              <SecretManager environment={primaryEnv} onBack={() => setShowAdvanced(false)} />
            </div>
            <div style={s.advancedCard}>
              <h4 style={s.advancedCardTitle}>Alert Rules</h4>
              <AlertRulesEditor environment={primaryEnv} onBack={() => setShowAdvanced(false)} />
            </div>
            <div style={s.advancedCard}>
              <h4 style={s.advancedCardTitle}>Pipeline YAML</h4>
              <PipelineYamlEditor />
            </div>
            <div style={s.advancedCard}>
              <h4 style={s.advancedCardTitle}>Scripts</h4>
              <ScriptRegistration onBack={() => setShowAdvanced(false)} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  header: { display: "flex", alignItems: "center", gap: "16px", marginBottom: "16px" },
  backBtn: { background: "none", border: "none", color: "#6c8aff", cursor: "pointer", fontSize: "0.9rem", padding: "4px 8px" },
  appName: { fontSize: "1.3rem", fontWeight: 700, color: "#e0e0e8", margin: 0 },
  envBadges: { display: "flex", gap: "8px", marginLeft: "auto" },
  envBadge: {
    display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "0.75rem", color: "#8888a0",
    padding: "4px 10px", borderRadius: "12px", border: "1px solid #5a5a70",
  },
  dot: { width: "6px", height: "6px", borderRadius: "50%", display: "inline-block" },
  tabBar: { display: "flex", borderBottom: "1px solid #1e1e2e", marginBottom: "16px" },
  tab: {
    background: "none", border: "none", borderBottom: "2px solid transparent",
    color: "#8888a0", padding: "10px 16px", fontSize: "0.85rem", fontWeight: 500, cursor: "pointer",
  },
  tabActive: { color: "#6c8aff", borderBottomColor: "#6c8aff" },
  content: { minHeight: "300px" },
  section: { backgroundColor: "#12121a", borderRadius: "12px", padding: "24px", border: "1px solid #1e1e2e" },
  sectionTitle: { fontSize: "1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 16px 0" },
  details: { display: "flex", flexDirection: "column", gap: "8px" },
  detailRow: { display: "flex", justifyContent: "space-between", fontSize: "0.9rem", color: "#e0e0e8", padding: "6px 0", borderBottom: "1px solid #1e1e2e" },
  detailLabel: { color: "#8888a0", fontWeight: 500 },
  actionBtn: {
    backgroundColor: "#2a2a3e", color: "#6c8aff", border: "none", borderRadius: "6px",
    padding: "8px 14px", fontSize: "0.85rem", cursor: "pointer", fontWeight: 500,
  },
  advancedToggle: { marginLeft: "auto", color: "#8888a0" },
  advancedDrawer: {
    marginTop: "24px", backgroundColor: "#12121a", borderRadius: "12px", padding: "24px",
    border: "1px solid #1e1e2e",
  },
  advancedGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" },
  advancedCard: { backgroundColor: "#0a0a0f", borderRadius: "8px", padding: "16px", border: "1px solid #1e1e2e" },
  advancedCardTitle: { fontSize: "0.9rem", fontWeight: 600, color: "#e0e0e8", margin: "0 0 12px 0" },
};
