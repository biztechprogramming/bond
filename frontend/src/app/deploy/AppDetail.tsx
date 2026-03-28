"use client";

import React, { useState, useMemo } from "react";
import { useComponents, useComponentResources, useEnvironments, useResources, useAlerts, useAgents } from "@/hooks/useSpacetimeDB";
import PipelineSection from "../settings/deployment/PipelineSection";
import MonitoringSection from "../settings/deployment/MonitoringSection";
import DeploymentTimeline from "../settings/deployment/DeploymentTimeline";
import LiveLogViewer from "../settings/deployment/LiveLogViewer";
import SecretManager from "../settings/deployment/SecretManager";
import AlertRulesEditor from "../settings/deployment/AlertRulesEditor";
import PipelineYamlEditor from "../settings/deployment/PipelineYamlEditor";
import ScriptRegistration from "../settings/deployment/ScriptRegistration";
import DeploymentRunsList from "./DeploymentRunsList";
import type { DeploymentRun } from "./DeploymentRunsList";
import { GATEWAY_API } from "@/lib/config";

type Tab = "overview" | "pipelines" | "monitoring" | "timeline" | "logs";

interface Props {
  appId: string; // component ID
  onBack: () => void;
}

export default function AppDetail({ appId, onBack }: Props) {
  const [tab, setTab] = useState<Tab>("overview");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [selectedOperator, setSelectedOperator] = useState<string | null>(null);
  const [rollbackStatus, setRollbackStatus] = useState<string | null>(null);

  const components = useComponents();
  const componentResources = useComponentResources(appId);
  const environments = useEnvironments();
  const resources = useResources();
  const alerts = useAlerts();
  const agents = useAgents();

  const component = useMemo(() => components.find((c) => c.id === appId), [components, appId]);
  const compAlerts = useMemo(() => alerts.filter((a) => a.componentId === appId), [alerts, appId]);

  const envLookup = useMemo(() => new Map(environments.map((e) => [e.name, e])), [environments]);
  const resourceLookup = useMemo(() => new Map(resources.map((r) => [r.id, r])), [resources]);

  const envBindings = useMemo(
    () =>
      componentResources.map((cr) => ({
        cr,
        env: envLookup.get(cr.environment),
        resource: resourceLookup.get(cr.resourceId),
      })),
    [componentResources, envLookup, resourceLookup]
  );

  const envNames = useMemo(() => envBindings.map((b) => b.cr.environment), [envBindings]);
  const primaryEnv = envNames[0] || "dev";

  const handleRollback = async (run: DeploymentRun) => {
    setRollbackStatus("rolling back...");
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          script_id: run.script_id,
          environment: run.environment,
          target_version: run.script_version,
        }),
      });
      if (res.ok) {
        setRollbackStatus("Rollback initiated");
      } else {
        setRollbackStatus("Rollback failed");
      }
    } catch {
      setRollbackStatus("Rollback failed — network error");
    }
    setTimeout(() => setRollbackStatus(null), 3000);
  };

  if (!component) {
    return (
      <div>
        <button style={s.backBtn} onClick={onBack}>&larr; Back</button>
        <p style={{ color: "#8888a0", textAlign: "center", marginTop: "40px" }}>Component not found.</p>
      </div>
    );
  }

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
        <h2 style={s.appName}>{component.displayName || component.name}</h2>
        <span style={s.typeBadge}>{component.componentType}</span>
        <div style={s.envBadges}>
          {envBindings.map((b) => (
            <span key={b.cr.id} style={{ ...s.envBadge, borderColor: b.cr.healthCheck ? "#6cffa0" : "#5a5a70" }}>
              <span style={{ ...s.dot, backgroundColor: b.cr.healthCheck ? "#6cffa0" : "#5a5a70" }} />
              {b.env?.displayName || b.cr.environment}
            </span>
          ))}
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

      {/* Rollback status toast */}
      {rollbackStatus && (
        <div style={s.toast}>{rollbackStatus}</div>
      )}

      {/* Tab content */}
      <div style={s.content}>
        {tab === "overview" && (
          <div style={s.section}>
            <h3 style={s.sectionTitle}>Component Overview</h3>
            <div style={s.details}>
              <div style={s.detailRow}><span style={s.detailLabel}>Name</span><span>{component.name}</span></div>
              <div style={s.detailRow}><span style={s.detailLabel}>Type</span><span>{component.componentType}</span></div>
              <div style={s.detailRow}><span style={s.detailLabel}>Framework</span><span>{component.framework || "\u2014"}</span></div>
              <div style={s.detailRow}><span style={s.detailLabel}>Runtime</span><span>{component.runtime || "\u2014"}</span></div>
              <div style={s.detailRow}><span style={s.detailLabel}>Repository</span><span style={{ fontFamily: "monospace", fontSize: "0.85rem" }}>{component.repositoryUrl || "\u2014"}</span></div>
              {component.description && (
                <div style={s.detailRow}><span style={s.detailLabel}>Description</span><span>{component.description}</span></div>
              )}
              <div style={s.detailRow}><span style={s.detailLabel}>Environments</span><span>{envBindings.length}</span></div>
              <div style={s.detailRow}><span style={s.detailLabel}>Active Alerts</span><span style={compAlerts.length > 0 ? { color: "#ff6c8a" } : {}}>{compAlerts.length}</span></div>
            </div>

            {/* Environment bindings */}
            {envBindings.length > 0 && (
              <div style={{ marginTop: "20px" }}>
                <h4 style={{ ...s.sectionTitle, fontSize: "0.9rem" }}>Environment Bindings</h4>
                {envBindings.map((b) => (
                  <div key={b.cr.id} style={s.envBinding}>
                    <span style={s.detailLabel}>{b.env?.displayName || b.cr.environment}</span>
                    <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>
                      {b.resource?.displayName || b.resource?.name || b.cr.resourceId}
                      {b.cr.port ? `:${b.cr.port}` : ""}
                    </span>
                    <span style={{ fontSize: "0.8rem", color: b.cr.healthCheck ? "#6cffa0" : "#5a5a70" }}>
                      {b.cr.healthCheck ? "Healthy" : "No health check"}
                    </span>
                  </div>
                ))}
              </div>
            )}

            {/* Deployments for this app */}
            <div style={{ marginTop: "24px" }}>
              <h4 style={{ ...s.sectionTitle, fontSize: "0.9rem" }}>Deployments</h4>
              <DeploymentRunsList
                environment={primaryEnv}
                limit={10}
                onRollback={handleRollback}
                onViewLogs={(run) => {
                  setTab("logs");
                }}
              />
            </div>

            {/* Assign Operator */}
            <div style={{ marginTop: "20px" }}>
              <h4 style={{ ...s.sectionTitle, fontSize: "0.9rem" }}>Assign Operator (AI Agent)</h4>
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
                {agents.map((agent) => (
                  <button
                    key={agent.id}
                    style={{
                      ...s.operatorBtn,
                      ...(selectedOperator === agent.id ? s.operatorBtnActive : {}),
                    }}
                    onClick={() => setSelectedOperator(selectedOperator === agent.id ? null : agent.id)}
                  >
                    {agent.displayName || agent.name}
                  </button>
                ))}
                {agents.length === 0 && <span style={{ color: "#5a5a70", fontSize: "0.85rem" }}>No agents available</span>}
              </div>
            </div>

            {/* Quick actions */}
            <div style={{ display: "flex", gap: "8px", marginTop: "16px" }}>
              <button style={s.actionBtn} onClick={() => setTab("pipelines")}>View Pipelines</button>
              <button style={s.actionBtn} onClick={() => setTab("logs")}>View Logs</button>
              <button style={s.actionBtn} onClick={() => setTab("monitoring")}>Monitoring</button>
              <button style={{ ...s.actionBtn, ...s.advancedToggle }} onClick={() => setShowAdvanced(!showAdvanced)}>
                {showAdvanced ? "Hide" : "Show"} Advanced
              </button>
            </div>
          </div>
        )}

        {tab === "pipelines" && (
          <PipelineSection environmentNames={envNames.length > 0 ? envNames : [primaryEnv]} />
        )}

        {tab === "monitoring" && (
          <MonitoringSection environment={primaryEnv} />
        )}

        {tab === "timeline" && (
          <DeploymentTimeline environments={envBindings.map((b) => ({ name: b.cr.environment, display_name: b.env?.displayName || b.cr.environment }))} />
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
  backBtn: { background: "none", borderWidth: 0, borderStyle: "none", borderColor: "transparent", color: "#6c8aff", cursor: "pointer", fontSize: "0.9rem", padding: "4px 8px" },
  appName: { fontSize: "1.3rem", fontWeight: 700, color: "#e0e0e8", margin: 0 },
  typeBadge: {
    fontSize: "0.7rem", fontWeight: 600, color: "#fff", padding: "2px 8px",
    borderRadius: "10px", backgroundColor: "#6c8aff", textTransform: "uppercase", letterSpacing: "0.5px",
  },
  envBadges: { display: "flex", gap: "8px", marginLeft: "auto" },
  envBadge: {
    display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "0.75rem", color: "#8888a0",
    padding: "4px 10px", borderRadius: "12px", borderWidth: "1px", borderStyle: "solid", borderColor: "#5a5a70",
  },
  dot: { width: "6px", height: "6px", borderRadius: "50%", display: "inline-block" },
  tabBar: { display: "flex", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", marginBottom: "16px" },
  tab: {
    background: "none", borderWidth: "0 0 2px 0", borderStyle: "solid", borderColor: "transparent",
    color: "#8888a0", padding: "10px 16px", fontSize: "0.85rem", fontWeight: 500, cursor: "pointer",
  },
  tabActive: { color: "#6c8aff", borderBottomColor: "#6c8aff" },
  content: { minHeight: "300px" },
  section: { backgroundColor: "#12121a", borderRadius: "12px", padding: "24px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },
  sectionTitle: { fontSize: "1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 16px 0" },
  details: { display: "flex", flexDirection: "column", gap: "8px" },
  detailRow: { display: "flex", justifyContent: "space-between", fontSize: "0.9rem", color: "#e0e0e8", padding: "6px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" },
  detailLabel: { color: "#8888a0", fontWeight: 500 },
  envBinding: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "8px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", fontSize: "0.9rem" },
  operatorBtn: {
    backgroundColor: "#1e1e2e", color: "#8888a0", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px",
    padding: "6px 12px", fontSize: "0.85rem", cursor: "pointer",
  },
  operatorBtnActive: { borderColor: "#6c8aff", color: "#6c8aff", backgroundColor: "rgba(108,138,255,0.1)" },
  actionBtn: {
    backgroundColor: "#2a2a3e", color: "#6c8aff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "6px",
    padding: "8px 14px", fontSize: "0.85rem", cursor: "pointer", fontWeight: 500,
  },
  advancedToggle: { marginLeft: "auto", color: "#8888a0" },
  advancedDrawer: {
    marginTop: "24px", backgroundColor: "#12121a", borderRadius: "12px", padding: "24px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
  },
  advancedGrid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" },
  advancedCard: { backgroundColor: "#0a0a0f", borderRadius: "8px", padding: "16px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },
  advancedCardTitle: { fontSize: "0.9rem", fontWeight: 600, color: "#e0e0e8", margin: "0 0 12px 0" },
  toast: {
    padding: "8px 16px", backgroundColor: "rgba(108,138,255,0.15)", borderRadius: "6px",
    color: "#6c8aff", fontSize: "0.85rem", marginBottom: "12px", textAlign: "center",
  },
};
