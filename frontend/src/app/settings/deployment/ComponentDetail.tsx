import React, { useEffect, useState, useCallback, useRef } from "react";
import { GATEWAY_API } from "@/lib/config";

interface ComponentDetailProps {
  componentId: string;
  onBack: () => void;
  onNavigate: (view: string, params?: Record<string, any>) => void;
}

interface ComponentData {
  id: string;
  display_name: string;
  component_type: string;
  runtime?: string;
  framework?: string;
  repository_url?: string;
  parent_id?: string;
  parent_name?: string;
  icon?: string;
  is_active: boolean;
}

interface ComponentResource {
  id: string;
  resource_id: string;
  resource_name: string;
  environment: string;
  port?: number;
  health_status?: "healthy" | "degraded" | "offline" | "unknown";
}

interface ComponentScript {
  id: string;
  script_id: string;
  script_name: string;
  role: string;
}

interface ComponentSecret {
  id: string;
  secret_key: string;
  environment: string;
  is_sensitive: boolean;
  masked_value: string;
}

interface DeploymentEntry {
  id: string;
  version: string;
  status: "success" | "failed" | "rolled_back" | "in_progress";
  environment: string;
  created_at: string;
  duration_seconds?: number;
  script_name?: string;
}

interface AlertRule {
  id: string;
  name: string;
  severity: "critical" | "warning" | "info";
  is_active: boolean;
}

interface RecentAlert {
  id: string;
  severity: "critical" | "warning" | "info";
  message: string;
  created_at: string;
}

// --- helpers ---

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function statusIcon(status: DeploymentEntry["status"]): { symbol: string; color: string } {
  switch (status) {
    case "success": return { symbol: "✓", color: "#6cffa0" };
    case "failed": return { symbol: "✗", color: "#ff6c8a" };
    case "rolled_back": return { symbol: "↩", color: "#ffcc6c" };
    case "in_progress": return { symbol: "◐", color: "#6c8aff" };
  }
}

function healthDot(status?: string): { symbol: string; color: string } {
  switch (status) {
    case "healthy": return { symbol: "●", color: "#6cffa0" };
    case "degraded": return { symbol: "◐", color: "#ffcc6c" };
    case "offline": return { symbol: "○", color: "#ff6c8a" };
    default: return { symbol: "⊘", color: "#5a5a6e" };
  }
}

function envHealthFromResources(resources: ComponentResource[], env: string): string {
  const envRes = resources.filter((r) => r.environment === env);
  if (envRes.length === 0) return "unknown";
  if (envRes.some((r) => r.health_status === "offline")) return "offline";
  if (envRes.some((r) => r.health_status === "degraded")) return "degraded";
  if (envRes.every((r) => r.health_status === "healthy")) return "healthy";
  return "unknown";
}

function typeBadgeColor(type: string): string {
  switch (type) {
    case "service": return "#6c8aff";
    case "database": return "#ffcc6c";
    case "queue": return "#ff6c8a";
    case "cache": return "#6cffa0";
    case "system": return "#8888a0";
    default: return "#8888a0";
  }
}

function roleLabel(role: string): string {
  const labels: Record<string, string> = {
    deploy: "Deploy",
    setup: "Setup",
    rollback: "Rollback",
    migrate: "Migrate",
    backup: "Backup",
  };
  return labels[role] || role;
}

function roleBadgeColor(role: string): string {
  switch (role) {
    case "deploy": return "#6c8aff";
    case "rollback": return "#ffcc6c";
    case "migrate": return "#ff6c8a";
    case "setup": return "#6cffa0";
    case "backup": return "#8888a0";
    default: return "#8888a0";
  }
}

// --- main component ---

export default function ComponentDetail({ componentId, onBack, onNavigate }: ComponentDetailProps) {
  const [component, setComponent] = useState<ComponentData | null>(null);
  const [resources, setResources] = useState<ComponentResource[]>([]);
  const [scripts, setScripts] = useState<ComponentScript[]>([]);
  const [secrets, setSecrets] = useState<ComponentSecret[]>([]);
  const [deployments, setDeployments] = useState<DeploymentEntry[]>([]);
  const [alertRules, setAlertRules] = useState<AlertRule[]>([]);
  const [recentAlerts, setRecentAlerts] = useState<RecentAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedEnv, setSelectedEnv] = useState<string | null>(null);
  const [secretsTab, setSecretsTab] = useState<string>("");
  const [revealedSecrets, setRevealedSecrets] = useState<Set<string>>(new Set());
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const environments = Array.from(new Set(resources.map((r) => r.environment)));
  const secretEnvironments = Array.from(new Set(secrets.map((s) => s.environment)));

  const fetchAll = useCallback(async () => {
    try {
      const [compRes, resRes, scriptRes, secretRes] = await Promise.all([
        fetch(`${GATEWAY_API}/deployments/components/${componentId}`),
        fetch(`${GATEWAY_API}/deployments/components/${componentId}/resources`),
        fetch(`${GATEWAY_API}/deployments/components/${componentId}/scripts`),
        fetch(`${GATEWAY_API}/deployments/components/${componentId}/secrets`),
      ]);

      if (compRes.ok) {
        const data = await compRes.json();
        setComponent(data.component || data);
        if (data.deployments) setDeployments(data.deployments);
        if (data.alert_rules) setAlertRules(data.alert_rules);
        if (data.recent_alerts) setRecentAlerts(data.recent_alerts);
      }
      if (resRes.ok) setResources(await resRes.json());
      if (scriptRes.ok) setScripts(await scriptRes.json());
      if (secretRes.ok) setSecrets(await secretRes.json());
    } catch { /* ignore */ }
    setLoading(false);
  }, [componentId]);

  const fetchStatus = useCallback(async () => {
    if (!selectedEnv) return;
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/components/${componentId}/status?environment=${encodeURIComponent(selectedEnv)}`);
      if (res.ok) {
        const data = await res.json();
        if (data.alert_rules) setAlertRules(data.alert_rules);
        if (data.recent_alerts) setRecentAlerts(data.recent_alerts);
      }
    } catch { /* ignore */ }
  }, [componentId, selectedEnv]);

  useEffect(() => {
    fetchAll();
    const i = setInterval(fetchStatus, 30_000);
    intervalRef.current = i;
    return () => clearInterval(i);
  }, [fetchAll, fetchStatus]);

  useEffect(() => {
    if (secretEnvironments.length > 0 && !secretsTab) {
      setSecretsTab(secretEnvironments[0]);
    }
  }, [secrets]);

  const handleReveal = (secretId: string) => {
    setRevealedSecrets((prev) => new Set(prev).add(secretId));
    setTimeout(() => {
      setRevealedSecrets((prev) => {
        const next = new Set(prev);
        next.delete(secretId);
        return next;
      });
    }, 10000);
  };

  if (loading) return <div style={{ color: "#8888a0", padding: 24 }}>Loading component...</div>;
  if (!component) return <div style={{ color: "#ff6c8a", padding: 24 }}>Component not found.</div>;

  const filteredDeployments = selectedEnv
    ? deployments.filter((d) => d.environment === selectedEnv)
    : deployments;

  const filteredResources = selectedEnv
    ? resources.filter((r) => r.environment === selectedEnv)
    : resources;

  return (
    <div style={st.root}>
      <style>{`@keyframes pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 0.8; } }`}</style>

      <div style={st.layout}>
        {/* --- Left Sidebar --- */}
        <div style={st.sidebar}>
          {/* Environments */}
          <div style={st.sideSection}>
            <h4 style={st.sideTitle}>Environments</h4>
            <button
              style={{ ...st.envItem, backgroundColor: !selectedEnv ? "#2a2a3e" : "transparent" }}
              onClick={() => setSelectedEnv(null)}
            >
              <span style={{ color: "#8888a0", fontSize: "0.7rem" }}>◉</span>
              <span style={st.envLabel}>All</span>
            </button>
            {environments.map((env) => {
              const h = healthDot(envHealthFromResources(resources, env));
              return (
                <button
                  key={env}
                  style={{ ...st.envItem, backgroundColor: selectedEnv === env ? "#2a2a3e" : "transparent" }}
                  onClick={() => setSelectedEnv(env)}
                >
                  <span style={{ color: h.color, fontSize: "0.7rem" }}>{h.symbol}</span>
                  <span style={st.envLabel}>{env}</span>
                </button>
              );
            })}
          </div>

          {/* Scripts */}
          {scripts.length > 0 && (
            <div style={st.sideSection}>
              <h4 style={st.sideTitle}>Scripts</h4>
              {scripts.map((sc) => (
                <div key={sc.id} style={st.scriptItem}>
                  <span style={st.scriptName}>{sc.script_name}</span>
                  <span style={{ ...st.roleBadge, color: roleBadgeColor(sc.role) }}>{roleLabel(sc.role)}</span>
                </div>
              ))}
            </div>
          )}

          {/* Runs On */}
          {filteredResources.length > 0 && (
            <div style={st.sideSection}>
              <h4 style={st.sideTitle}>Runs On</h4>
              {filteredResources.map((r) => (
                <div key={r.id} style={st.resourceItem}>
                  <span style={{ color: healthDot(r.health_status).color, fontSize: "0.6rem" }}>{healthDot(r.health_status).symbol}</span>
                  <span style={st.resourceName}>{r.resource_name}{r.port ? `:${r.port}` : ""}</span>
                  {!selectedEnv && <span style={st.resourceEnv}>{r.environment}</span>}
                </div>
              ))}
            </div>
          )}

          <button style={st.backBtn} onClick={onBack}>← Back</button>
        </div>

        {/* --- Main Content --- */}
        <div style={st.main}>
          {/* Header */}
          <div style={st.header}>
            <div style={st.headerLeft}>
              {component.icon && <span style={{ fontSize: "1.4rem" }}>{component.icon}</span>}
              <div>
                <h2 style={st.compTitle}>{component.display_name}</h2>
                <div style={st.headerMeta}>
                  <span style={{ ...st.typeBadge, color: typeBadgeColor(component.component_type) }}>{component.component_type}</span>
                  {component.runtime && <span style={st.metaText}>{component.runtime}</span>}
                  {component.framework && <span style={st.metaText}>{component.framework}</span>}
                  {component.repository_url && (
                    <a href={component.repository_url} target="_blank" rel="noreferrer" style={st.repoLink}>repo →</a>
                  )}
                  {component.parent_name && (
                    <span style={st.metaText}>in {component.parent_name}</span>
                  )}
                </div>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button style={st.actionBtn} onClick={() => onNavigate("edit-component", { componentId })}>Edit</button>
              <button style={{ ...st.actionBtn, color: "#ff6c8a", borderColor: "#ff6c8a33" }}>Deactivate</button>
            </div>
          </div>

          {/* Deployment History */}
          <div style={st.section}>
            <h3 style={st.sectionTitle}>Deployment History</h3>
            {filteredDeployments.length === 0 ? (
              <p style={st.empty}>No deployments recorded.</p>
            ) : (
              <div style={st.table}>
                <div style={st.tableHeader}>
                  <span style={{ ...st.th, width: 80 }}>Version</span>
                  <span style={{ ...st.th, width: 30 }}>St</span>
                  <span style={{ ...st.th, flex: 1 }}>Environment</span>
                  <span style={{ ...st.th, width: 100 }}>Date</span>
                  <span style={{ ...st.th, width: 60 }}>Duration</span>
                  <span style={{ ...st.th, flex: 1 }}>Script</span>
                </div>
                {filteredDeployments.map((d) => {
                  const icon = statusIcon(d.status);
                  return (
                    <div key={d.id} style={st.tableRow}>
                      <span style={{ ...st.td, width: 80, fontFamily: "monospace" }}>{d.version}</span>
                      <span style={{ ...st.td, width: 30, color: icon.color, fontWeight: 700 }}>{icon.symbol}</span>
                      <span style={{ ...st.td, flex: 1 }}>{d.environment}</span>
                      <span style={{ ...st.td, width: 100, color: "#8888a0" }}>{relativeTime(d.created_at)}</span>
                      <span style={{ ...st.td, width: 60, color: "#8888a0", fontFamily: "monospace" }}>
                        {d.duration_seconds != null ? `${d.duration_seconds}s` : "—"}
                      </span>
                      <span style={{ ...st.td, flex: 1, color: "#8888a0" }}>{d.script_name || "—"}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Secrets */}
          {secrets.length > 0 && (
            <div style={st.section}>
              <h3 style={st.sectionTitle}>Secrets</h3>
              <div style={{ display: "flex", gap: 2, marginBottom: 12 }}>
                {secretEnvironments.map((env) => (
                  <button
                    key={env}
                    onClick={() => setSecretsTab(env)}
                    style={{
                      ...st.tabBtn,
                      backgroundColor: secretsTab === env ? "#6c8aff" : "transparent",
                      color: secretsTab === env ? "#fff" : "#8888a0",
                    }}
                  >
                    {env}
                  </button>
                ))}
              </div>
              <div style={st.table}>
                <div style={st.tableHeader}>
                  <span style={{ ...st.th, flex: 1 }}>Key</span>
                  <span style={{ ...st.th, width: 200 }}>Value</span>
                  <span style={{ ...st.th, width: 80 }}>Sensitive</span>
                  <span style={{ ...st.th, width: 60 }}></span>
                </div>
                {secrets.filter((s) => s.environment === secretsTab).map((sec) => (
                  <div key={sec.id} style={st.tableRow}>
                    <span style={{ ...st.td, flex: 1, fontFamily: "monospace" }}>{sec.secret_key}</span>
                    <span style={{ ...st.td, width: 200, fontFamily: "monospace", color: "#8888a0" }}>
                      {revealedSecrets.has(sec.id) ? sec.masked_value : "••••••••"}
                    </span>
                    <span style={{ ...st.td, width: 80 }}>
                      {sec.is_sensitive && <span style={{ color: "#ff6c8a", fontSize: "0.75rem" }}>sensitive</span>}
                    </span>
                    <span style={{ ...st.td, width: 60 }}>
                      <button style={st.revealBtn} onClick={() => handleReveal(sec.id)}>
                        {revealedSecrets.has(sec.id) ? "hide" : "reveal"}
                      </button>
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Monitoring */}
          <div style={st.section}>
            <h3 style={st.sectionTitle}>Monitoring</h3>
            <div style={{ display: "flex", gap: 24 }}>
              {/* Health check */}
              <div style={{ flex: 1 }}>
                <h4 style={st.subTitle}>Health Checks</h4>
                {filteredResources.length === 0 ? (
                  <p style={st.empty}>No resources linked.</p>
                ) : (
                  filteredResources.map((r) => {
                    const h = healthDot(r.health_status);
                    return (
                      <div key={r.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: "0.82rem" }}>
                        <span style={{ color: h.color, fontSize: "0.65rem" }}>{h.symbol}</span>
                        <span style={{ color: "#e0e0e8" }}>{r.resource_name}{r.port ? `:${r.port}` : ""}</span>
                        <span style={{ color: "#8888a0", fontSize: "0.75rem" }}>{r.environment}</span>
                      </div>
                    );
                  })
                )}
              </div>

              {/* Alert rules */}
              <div style={{ flex: 1 }}>
                <h4 style={st.subTitle}>Alert Rules</h4>
                {alertRules.length === 0 ? (
                  <p style={st.empty}>No alert rules.</p>
                ) : (
                  alertRules.map((rule) => (
                    <div key={rule.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: "0.82rem" }}>
                      <span style={{ color: rule.is_active ? "#6cffa0" : "#5a5a6e", fontSize: "0.55rem" }}>●</span>
                      <span style={{ color: "#e0e0e8", flex: 1 }}>{rule.name}</span>
                      <span style={{ color: rule.severity === "critical" ? "#ff6c8a" : rule.severity === "warning" ? "#ffcc6c" : "#6c8aff", fontSize: "0.75rem" }}>{rule.severity}</span>
                    </div>
                  ))
                )}
              </div>

              {/* Recent alerts */}
              <div style={{ flex: 1 }}>
                <h4 style={st.subTitle}>Recent Alerts</h4>
                {recentAlerts.length === 0 ? (
                  <p style={st.empty}>No recent alerts.</p>
                ) : (
                  recentAlerts.slice(0, 5).map((a) => (
                    <div key={a.id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", fontSize: "0.82rem" }}>
                      <span style={{ color: a.severity === "critical" ? "#ff6c8a" : a.severity === "warning" ? "#ffcc6c" : "#6c8aff", fontSize: "0.55rem" }}>●</span>
                      <span style={{ color: "#e0e0e8", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.message}</span>
                      <span style={{ color: "#5a5a6e", fontSize: "0.75rem", whiteSpace: "nowrap" }}>{relativeTime(a.created_at)}</span>
                    </div>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- styles ---

const st: Record<string, React.CSSProperties> = {
  root: { display: "flex", flexDirection: "column", gap: 16 },
  layout: { display: "flex", gap: 20 },

  // Sidebar
  sidebar: { width: 220, flexShrink: 0, display: "flex", flexDirection: "column", gap: 20 },
  sideSection: { display: "flex", flexDirection: "column", gap: 4 },
  sideTitle: { fontSize: "0.78rem", fontWeight: 600, color: "#6c8aff", margin: 0, marginBottom: 4, textTransform: "uppercase", letterSpacing: "0.5px" },
  envItem: {
    display: "flex", alignItems: "center", gap: 8, padding: "6px 10px",
    border: "none", borderRadius: 6, cursor: "pointer", fontSize: "0.82rem", color: "#e0e0e8", textAlign: "left",
  },
  envLabel: { flex: 1 },
  scriptItem: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "4px 0", fontSize: "0.8rem" },
  scriptName: { color: "#e0e0e8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  roleBadge: { fontSize: "0.7rem", fontWeight: 600 },
  resourceItem: { display: "flex", alignItems: "center", gap: 6, padding: "3px 0", fontSize: "0.8rem" },
  resourceName: { color: "#e0e0e8", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  resourceEnv: { color: "#5a5a6e", fontSize: "0.7rem" },
  backBtn: {
    background: "none", border: "1px solid #3a3a4e", borderRadius: 8,
    color: "#8888a0", padding: "8px 12px", fontSize: "0.82rem", cursor: "pointer", marginTop: "auto",
  },

  // Main
  main: { flex: 1, display: "flex", flexDirection: "column", gap: 20, minWidth: 0 },

  // Header
  header: { display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 16 },
  headerLeft: { display: "flex", alignItems: "center", gap: 12 },
  compTitle: { fontSize: "1.3rem", fontWeight: 700, color: "#e0e0e8", margin: 0 },
  headerMeta: { display: "flex", alignItems: "center", gap: 10, marginTop: 4, flexWrap: "wrap" },
  typeBadge: { fontSize: "0.75rem", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.5px" },
  metaText: { fontSize: "0.8rem", color: "#8888a0" },
  repoLink: { fontSize: "0.8rem", color: "#6c8aff", textDecoration: "none" },
  actionBtn: {
    backgroundColor: "#2a2a3e", color: "#e0e0e8", border: "1px solid #3a3a4e",
    borderRadius: 8, padding: "8px 16px", fontSize: "0.82rem", cursor: "pointer",
  },

  // Sections
  section: {
    backgroundColor: "#12121a", border: "1px solid #1e1e2e", borderRadius: 10,
    padding: 16, display: "flex", flexDirection: "column", gap: 12,
  },
  sectionTitle: { fontSize: "0.9rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  subTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", margin: "0 0 6px 0" },
  empty: { fontSize: "0.82rem", color: "#5a5a6e", margin: 0 },

  // Table
  table: { display: "flex", flexDirection: "column" },
  tableHeader: { display: "flex", gap: 8, padding: "6px 0", borderBottom: "1px solid #2a2a3e" },
  th: { fontSize: "0.72rem", fontWeight: 600, color: "#5a5a6e", textTransform: "uppercase", letterSpacing: "0.5px" },
  tableRow: { display: "flex", gap: 8, padding: "6px 0", borderBottom: "1px solid #1e1e2e", alignItems: "center" },
  td: { fontSize: "0.82rem", color: "#e0e0e8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },

  // Tabs & buttons
  tabBtn: { border: "none", borderRadius: 6, padding: "4px 12px", fontSize: "0.78rem", fontWeight: 600, cursor: "pointer" },
  revealBtn: {
    background: "none", border: "1px solid #3a3a4e", borderRadius: 4,
    color: "#6c8aff", fontSize: "0.7rem", padding: "2px 8px", cursor: "pointer",
  },
};
