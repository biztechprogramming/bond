import React, { useEffect, useState, useCallback, useRef } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Agent {
  id: string;
  name: string;
  display_name: string;
  model: string;
  utility_model: string;
  is_active: boolean;
}

interface EnvironmentDashboardProps {
  environment: { name: string; display_name: string };
  agents: Agent[];
  onNavigate: (view: string, params?: Record<string, any>) => void;
}

interface ComponentNode {
  id: string;
  display_name: string;
  component_type: string;
  runtime?: string;
  framework?: string;
  icon?: string;
  is_active: boolean;
  resource_name?: string;
  port?: number;
  health_status?: "healthy" | "degraded" | "offline" | "unknown";
  last_deploy?: {
    script_name: string;
    version: string;
    created_at: string;
    status: "success" | "failed" | "rolled_back" | "in_progress";
  };
  secrets_count?: number;
  children?: ComponentNode[];
}

interface ServerStatus {
  resource_id: string;
  name: string;
  display_name: string;
  status: "online" | "degraded" | "offline" | "unknown";
  cpu_percent: number;
  ram_percent: number;
  disk_percent: number;
  last_probe: string;
}

interface DeploymentReceipt {
  id: string;
  script_name: string;
  version: string;
  status: "success" | "failed" | "rolled_back" | "in_progress";
  created_at: string;
}

interface MonitoringAlert {
  id: string;
  severity: "critical" | "warning" | "info";
  message: string;
  created_at: string;
}

// --- helpers ---

function gaugeColor(pct: number): string {
  if (pct >= 90) return "#ff6c8a";
  if (pct >= 70) return "#ffcc6c";
  return "#6cffa0";
}

function statusDot(status: ServerStatus["status"]): { symbol: string; color: string } {
  switch (status) {
    case "online": return { symbol: "●", color: "#6cffa0" };
    case "degraded": return { symbol: "◐", color: "#ffcc6c" };
    case "offline": return { symbol: "○", color: "#ff6c8a" };
    default: return { symbol: "⊘", color: "#5a5a6e" };
  }
}

function receiptIcon(status: DeploymentReceipt["status"]): { symbol: string; color: string } {
  switch (status) {
    case "success": return { symbol: "✓", color: "#6cffa0" };
    case "failed": return { symbol: "✗", color: "#ff6c8a" };
    case "rolled_back": return { symbol: "↩", color: "#ffcc6c" };
    case "in_progress": return { symbol: "◐", color: "#6c8aff" };
  }
}

function severityColor(s: MonitoringAlert["severity"]): string {
  switch (s) {
    case "critical": return "#ff6c8a";
    case "warning": return "#ffcc6c";
    case "info": return "#6c8aff";
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

function deployStatusIcon(status: string): { symbol: string; color: string } {
  switch (status) {
    case "success": return { symbol: "✓", color: "#6cffa0" };
    case "failed": return { symbol: "✗", color: "#ff6c8a" };
    case "rolled_back": return { symbol: "↩", color: "#ffcc6c" };
    case "in_progress": return { symbol: "◐", color: "#6c8aff" };
    default: return { symbol: "—", color: "#5a5a6e" };
  }
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

function overallHealth(servers: ServerStatus[]): { label: string; symbol: string; color: string } {
  if (servers.length === 0) return { label: "No servers", symbol: "○", color: "#5a5a6e" };
  if (servers.some((s) => s.status === "offline")) return { label: "Offline", symbol: "○", color: "#ff6c8a" };
  if (servers.some((s) => s.status === "degraded")) return { label: "Degraded", symbol: "◐", color: "#ffcc6c" };
  if (servers.every((s) => s.status === "online")) return { label: "Healthy", symbol: "●", color: "#6cffa0" };
  return { label: "Partial", symbol: "◐", color: "#8888a0" };
}

function componentsOverallHealth(nodes: ComponentNode[]): { label: string; symbol: string; color: string } {
  const all: ComponentNode[] = [];
  const collect = (ns: ComponentNode[]) => { for (const n of ns) { all.push(n); if (n.children) collect(n.children); } };
  collect(nodes);
  if (all.length === 0) return { label: "No components", symbol: "○", color: "#5a5a6e" };
  if (all.some((c) => c.health_status === "offline")) return { label: "Offline", symbol: "○", color: "#ff6c8a" };
  if (all.some((c) => c.health_status === "degraded")) return { label: "Degraded", symbol: "◐", color: "#ffcc6c" };
  if (all.every((c) => c.health_status === "healthy")) return { label: "Healthy", symbol: "●", color: "#6cffa0" };
  return { label: "Partial", symbol: "◐", color: "#8888a0" };
}

// --- skeleton ---

function Skeleton({ width, height = 14 }: { width: number | string; height?: number }) {
  return (
    <div style={{ width, height, backgroundColor: "#2a2a3e", borderRadius: 4, animation: "pulse 1.5s ease-in-out infinite" }} />
  );
}

// --- gauge bar ---

function Gauge({ label, percent }: { label: string; percent: number }) {
  const color = gaugeColor(percent);
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.75rem" }}>
      <span style={{ color: "#8888a0", width: 30 }}>{label}</span>
      <div style={{ flex: 1, height: 6, backgroundColor: "#1a1a2e", borderRadius: 3, overflow: "hidden" }}>
        <div style={{ width: `${Math.min(percent, 100)}%`, height: "100%", backgroundColor: color, borderRadius: 3, transition: "width 0.4s" }} />
      </div>
      <span style={{ color, width: 36, textAlign: "right", fontFamily: "monospace" }}>{Math.round(percent)}%</span>
    </div>
  );
}

// --- component card ---

function ComponentCard({ node, onNavigate, depth = 0 }: { node: ComponentNode; onNavigate: EnvironmentDashboardProps["onNavigate"]; depth?: number }) {
  const [collapsed, setCollapsed] = useState(false);
  const isSystem = node.component_type === "system" && node.children && node.children.length > 0;
  const h = healthDot(node.health_status);
  const deploy = node.last_deploy;
  const dIcon = deploy ? deployStatusIcon(deploy.status) : null;

  return (
    <div style={{ marginLeft: depth * 20 }}>
      <div
        style={s.compCard}
        onClick={() => {
          if (isSystem) setCollapsed((c) => !c);
          else onNavigate("component-detail", { componentId: node.id });
        }}
      >
        <div style={s.compCardTop}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flex: 1, minWidth: 0 }}>
            {isSystem && <span style={{ color: "#8888a0", fontSize: "0.7rem" }}>{collapsed ? "▶" : "▼"}</span>}
            <span style={{ fontSize: "1rem" }}>{node.icon || "📦"}</span>
            <span style={s.compName}>{node.display_name}</span>
          </div>
          <span style={{ color: h.color, fontSize: "0.7rem" }}>{h.symbol} {node.health_status || "unknown"}</span>
        </div>
        <div style={s.compCardBottom}>
          <span style={s.compMeta}>
            {node.runtime || node.component_type}{node.framework ? ` / ${node.framework}` : ""}
            {node.resource_name ? ` · ${node.resource_name}${node.port ? `:${node.port}` : ""}` : ""}
          </span>
          {deploy && (
            <span style={s.compMeta}>
              {deploy.script_name} v{deploy.version} · {relativeTime(deploy.created_at)}{" "}
              <span style={{ color: dIcon!.color }}>{dIcon!.symbol}</span>
            </span>
          )}
          {node.secrets_count != null && node.secrets_count > 0 && (
            <span style={s.compMeta}> · {node.secrets_count} secrets</span>
          )}
        </div>
      </div>
      {isSystem && !collapsed && node.children!.map((child) => (
        <ComponentCard key={child.id} node={child} onNavigate={onNavigate} depth={depth + 1} />
      ))}
    </div>
  );
}

// --- main component ---

export default function EnvironmentDashboard({ environment, agents, onNavigate }: EnvironmentDashboardProps) {
  const [components, setComponents] = useState<ComponentNode[] | null>(null);
  const [componentsFailed, setComponentsFailed] = useState(false);
  const [servers, setServers] = useState<ServerStatus[] | null>(null);
  const [receipts, setReceipts] = useState<DeploymentReceipt[] | null>(null);
  const [alerts, setAlerts] = useState<MonitoringAlert[] | null>(null);
  const intervalsRef = useRef<ReturnType<typeof setInterval>[]>([]);

  const envName = environment.name;

  const fetchComponents = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/components?environment=${encodeURIComponent(envName)}&tree=true`);
      if (res.ok) {
        const data = await res.json();
        const list = Array.isArray(data) ? data : (data.components || []);
        setComponents(list);
        setComponentsFailed(list.length === 0);
      } else {
        setComponents([]);
        setComponentsFailed(true);
      }
    } catch {
      setComponents([]);
      setComponentsFailed(true);
    }
  }, [envName]);

  const fetchServers = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/resources?environment=${encodeURIComponent(envName)}`);
      if (res.ok) {
        const resources: any[] = await res.json();
        const mapped: ServerStatus[] = resources.map((r: any) => {
          const state = typeof r.state_json === "string" ? (() => { try { return JSON.parse(r.state_json); } catch { return {}; } })() : (r.state_json || {});
          const caps = typeof r.capabilities_json === "string" ? (() => { try { return JSON.parse(r.capabilities_json); } catch { return {}; } })() : (r.capabilities_json || {});
          return {
            resource_id: r.id,
            name: r.name,
            display_name: r.display_name || r.name,
            status: state.status || (r.is_active ? (r.last_probed_at ? "online" : "unknown") : "offline"),
            cpu_percent: state.cpu_percent ?? caps.cpu_percent ?? 0,
            ram_percent: state.ram_percent ?? caps.ram_percent ?? 0,
            disk_percent: state.disk_percent ?? caps.disk_percent ?? 0,
            last_probe: r.last_probed_at ? new Date(typeof r.last_probed_at === "number" ? r.last_probed_at : r.last_probed_at).toISOString() : new Date(0).toISOString(),
          };
        });
        setServers(mapped);
      } else setServers([]);
    } catch { setServers([]); }
  }, [envName]);

  const fetchReceipts = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/receipts/${encodeURIComponent(envName)}?limit=10`);
      if (res.ok) {
        const data = await res.json();
        const mapped: DeploymentReceipt[] = (Array.isArray(data) ? data : []).map((r: any) => ({
          id: r.id || r.receipt_id || "",
          script_name: r.script_name || r.script_id || "unknown",
          version: r.version || r.script_version || "?",
          status: r.status || "success",
          created_at: r.created_at || r.timestamp || new Date().toISOString(),
        }));
        setReceipts(mapped);
      } else setReceipts([]);
    } catch { setReceipts([]); }
  }, [envName]);

  const fetchAlerts = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/monitoring/${encodeURIComponent(envName)}`);
      if (res.ok) {
        const data = await res.json();
        const alertList = Array.isArray(data) ? data : (data.recent_alerts || []);
        setAlerts(alertList);
      } else setAlerts([]);
    } catch { setAlerts([]); }
  }, [envName]);

  useEffect(() => {
    fetchComponents();
    fetchServers();
    fetchReceipts();
    fetchAlerts();

    const i1 = setInterval(fetchComponents, 30_000);
    const i2 = setInterval(fetchServers, 30_000);
    const i3 = setInterval(fetchReceipts, 60_000);
    const i4 = setInterval(fetchAlerts, 30_000);
    intervalsRef.current = [i1, i2, i3, i4];

    return () => intervalsRef.current.forEach(clearInterval);
  }, [fetchComponents, fetchServers, fetchReceipts, fetchAlerts]);

  const useFallback = componentsFailed || (components !== null && components.length === 0);

  const health = components && !useFallback
    ? componentsOverallHealth(components)
    : servers ? overallHealth(servers) : null;

  const lastDeploy = receipts && receipts.length > 0 ? receipts[0] : null;
  const envAgent = agents.find((a) => a.name === `deploy-${envName}`);

  const alertCounts = alerts
    ? { critical: alerts.filter((a) => a.severity === "critical").length, warning: alerts.filter((a) => a.severity === "warning").length, info: alerts.filter((a) => a.severity === "info").length }
    : null;

  const quickActions: { label: string; view: string }[] = [
    { label: "+ Component", view: "add-component" },
    { label: "+ Server", view: "onboard-server" },
    { label: "Deploy Script", view: "deploy-script" },
    { label: "Run Discovery", view: "run-discovery" },
    { label: "View Logs", view: "live-logs" },
    { label: "Check Health", view: "check-health" },
    { label: "Agent Settings", view: "agent-settings" },
  ];

  return (
    <div style={s.root}>
      <style>{`@keyframes pulse { 0%,100% { opacity: 0.4; } 50% { opacity: 0.8; } }`}</style>

      {/* --- header --- */}
      <div style={s.header}>
        <div style={s.headerLeft}>
          <h2 style={s.envTitle}>{environment.display_name}</h2>
          {health ? (
            <span style={{ ...s.healthBadge, color: health.color }}>{health.symbol} {health.label}</span>
          ) : (
            <Skeleton width={80} />
          )}
        </div>
        <div style={s.headerRight}>
          {lastDeploy && (
            <span style={s.lastDeploy}>
              Last deploy: {lastDeploy.script_name} v{lastDeploy.version} &middot; {relativeTime(lastDeploy.created_at)}
            </span>
          )}
          {envAgent && (
            <span style={s.agentStatus}>
              Agent: <span style={{ color: envAgent.is_active ? "#6cffa0" : "#ff6c8a" }}>{envAgent.is_active ? "active" : "inactive"}</span>
            </span>
          )}
        </div>
      </div>

      {/* --- Component tree view (primary) --- */}
      {!useFallback && components !== null && (
        <div style={s.compTree}>
          {components.length === 0 ? (
            <p style={s.empty}>No components registered.</p>
          ) : (
            components.map((node) => (
              <ComponentCard key={node.id} node={node} onNavigate={onNavigate} />
            ))
          )}
        </div>
      )}

      {/* --- Loading state for components --- */}
      {components === null && (
        <div style={s.compTree}>
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} style={{ ...s.compCard, gap: 8 }}>
              <Skeleton width="50%" height={16} />
              <Skeleton width="80%" height={12} />
            </div>
          ))}
        </div>
      )}

      {/* --- Fallback: 3-column layout (servers/receipts/alerts) --- */}
      {useFallback && (
        <div style={s.columns}>
          {/* LEFT: Servers */}
          <div style={s.column}>
            <h3 style={s.colTitle}>Servers</h3>
            <div style={s.colBody}>
              {servers === null ? (
                Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} style={s.card}><Skeleton width="60%" height={16} /><Skeleton width="100%" height={6} /><Skeleton width="100%" height={6} /><Skeleton width="100%" height={6} /></div>
                ))
              ) : servers.length === 0 ? (
                <p style={s.empty}>No servers registered.</p>
              ) : (
                servers.map((srv) => {
                  const dot = statusDot(srv.status);
                  return (
                    <div key={srv.resource_id} style={s.card}>
                      <div style={s.serverHeader}>
                        <span style={{ color: dot.color, fontSize: "0.7rem" }}>{dot.symbol}</span>
                        <span style={s.serverName}>{srv.display_name || srv.name}</span>
                        <span style={s.serverProbe}>{relativeTime(srv.last_probe)}</span>
                      </div>
                      <Gauge label="CPU" percent={srv.cpu_percent} />
                      <Gauge label="RAM" percent={srv.ram_percent} />
                      <Gauge label="Disk" percent={srv.disk_percent} />
                    </div>
                  );
                })
              )}
              <button style={s.addBtn} onClick={() => onNavigate("onboard-server")}>+ Add Server</button>
            </div>
          </div>

          {/* CENTER: Recent Deployments */}
          <div style={s.column}>
            <h3 style={s.colTitle}>Recent Deployments</h3>
            <div style={s.colBody}>
              {receipts === null ? (
                Array.from({ length: 5 }).map((_, i) => (
                  <div key={i} style={{ ...s.receiptRow, gap: 8 }}><Skeleton width={16} height={16} /><Skeleton width="70%" /></div>
                ))
              ) : receipts.length === 0 ? (
                <p style={s.empty}>No deployments yet.</p>
              ) : (
                receipts.map((r) => {
                  const icon = receiptIcon(r.status);
                  return (
                    <div key={r.id} style={s.receiptRow}>
                      <span style={{ color: icon.color, fontWeight: 700, width: 18, textAlign: "center" }}>{icon.symbol}</span>
                      <span style={s.receiptName}>{r.script_name}</span>
                      <span style={s.receiptVersion}>v{r.version}</span>
                      <span style={s.receiptTime}>{relativeTime(r.created_at)}</span>
                    </div>
                  );
                })
              )}
              {receipts && receipts.length > 0 && (
                <button style={s.linkBtn} onClick={() => onNavigate("receipts", { environment: envName })}>View all receipts →</button>
              )}
            </div>
          </div>

          {/* RIGHT: Alerts */}
          <div style={s.column}>
            <h3 style={s.colTitle}>Alerts</h3>
            {alertCounts && (
              <div style={s.alertSummary}>
                {alertCounts.critical > 0 && <span style={{ color: "#ff6c8a" }}>{alertCounts.critical} critical</span>}
                {alertCounts.warning > 0 && <span style={{ color: "#ffcc6c" }}>{alertCounts.warning} warning</span>}
                {alertCounts.info > 0 && <span style={{ color: "#6c8aff" }}>{alertCounts.info} info</span>}
              </div>
            )}
            <div style={s.colBody}>
              {alerts === null ? (
                Array.from({ length: 4 }).map((_, i) => (
                  <div key={i} style={{ ...s.alertRow, gap: 8 }}><Skeleton width={8} height={8} /><Skeleton width="80%" /></div>
                ))
              ) : alerts.length === 0 ? (
                <p style={s.empty}>No alerts.</p>
              ) : (
                alerts.map((a) => (
                  <div key={a.id} style={s.alertRow}>
                    <span style={{ color: severityColor(a.severity), fontSize: "0.55rem" }}>●</span>
                    <span style={s.alertMsg}>{a.message}</span>
                    <span style={s.alertTime}>{relativeTime(a.created_at)}</span>
                  </div>
                ))
              )}
              {alerts && alerts.length > 0 && (
                <button style={s.linkBtn} onClick={() => onNavigate("alerts", { environment: envName })}>View all →</button>
              )}
            </div>
          </div>
        </div>
      )}

      {/* --- Infrastructure summary --- */}
      {!useFallback && (servers || alertCounts) && (
        <div style={s.infraSummary}>
          {servers && <span style={s.infraItem}>{servers.length} server{servers.length !== 1 ? "s" : ""}</span>}
          {alertCounts && alertCounts.critical > 0 && <span style={{ ...s.infraItem, color: "#ff6c8a" }}>{alertCounts.critical} critical</span>}
          {alertCounts && alertCounts.warning > 0 && <span style={{ ...s.infraItem, color: "#ffcc6c" }}>{alertCounts.warning} warning</span>}
          {alertCounts && alertCounts.info > 0 && <span style={{ ...s.infraItem, color: "#6c8aff" }}>{alertCounts.info} info</span>}
        </div>
      )}

      {/* --- quick actions --- */}
      <div style={s.quickActions}>
        {quickActions.map((qa) => (
          <button key={qa.view} style={s.qaBtn} onClick={() => onNavigate(qa.view, { environment: envName })}>
            {qa.label}
          </button>
        ))}
      </div>
    </div>
  );
}

// --- styles ---

const s: Record<string, React.CSSProperties> = {
  root: { display: "flex", flexDirection: "column", gap: 20 },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 },
  headerLeft: { display: "flex", alignItems: "center", gap: 12 },
  envTitle: { fontSize: "1.3rem", fontWeight: 700, color: "#e0e0e8", margin: 0 },
  healthBadge: { fontSize: "0.85rem", fontWeight: 600 },
  headerRight: { display: "flex", alignItems: "center", gap: 16, fontSize: "0.82rem", color: "#8888a0" },
  lastDeploy: {},
  agentStatus: {},

  // Component tree
  compTree: { display: "flex", flexDirection: "column", gap: 8 },
  compCard: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: 10,
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 4,
    cursor: "pointer",
    transition: "border-color 0.2s",
  },
  compCardTop: { display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8 },
  compCardBottom: { display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" },
  compName: { fontSize: "0.9rem", fontWeight: 600, color: "#e0e0e8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  compMeta: { fontSize: "0.78rem", color: "#8888a0" },

  // Infrastructure summary
  infraSummary: { display: "flex", gap: 16, fontSize: "0.8rem", color: "#8888a0", padding: "8px 0", borderTop: "1px solid #1e1e2e" },
  infraItem: { fontWeight: 600 },

  // Fallback columns
  columns: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 16 },
  column: { display: "flex", flexDirection: "column", gap: 8 },
  colTitle: { fontSize: "0.9rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  colBody: { display: "flex", flexDirection: "column", gap: 8, flex: 1 },

  card: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: 10,
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 6,
  },
  serverHeader: { display: "flex", alignItems: "center", gap: 6 },
  serverName: { fontSize: "0.88rem", fontWeight: 600, color: "#e0e0e8", flex: 1 },
  serverProbe: { fontSize: "0.7rem", color: "#5a5a6e" },

  receiptRow: { display: "flex", alignItems: "center", gap: 8, fontSize: "0.82rem", padding: "4px 0", borderBottom: "1px solid #1e1e2e" },
  receiptName: { color: "#e0e0e8", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  receiptVersion: { color: "#8888a0", fontFamily: "monospace", fontSize: "0.75rem" },
  receiptTime: { color: "#5a5a6e", fontSize: "0.75rem", whiteSpace: "nowrap" },

  alertSummary: { display: "flex", gap: 12, fontSize: "0.78rem", fontWeight: 600 },
  alertRow: { display: "flex", alignItems: "center", gap: 8, fontSize: "0.82rem", padding: "4px 0", borderBottom: "1px solid #1e1e2e" },
  alertMsg: { color: "#e0e0e8", flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  alertTime: { color: "#5a5a6e", fontSize: "0.75rem", whiteSpace: "nowrap" },

  empty: { fontSize: "0.82rem", color: "#5a5a6e", margin: 0 },

  addBtn: {
    backgroundColor: "#1a1a2e",
    color: "#6c8aff",
    border: "1px dashed #3a3a4e",
    borderRadius: 8,
    padding: "10px",
    fontSize: "0.82rem",
    cursor: "pointer",
    textAlign: "center",
    marginTop: 4,
  },
  linkBtn: {
    background: "none",
    border: "none",
    color: "#6c8aff",
    fontSize: "0.78rem",
    cursor: "pointer",
    padding: "4px 0",
    textAlign: "left",
  },
  quickActions: { display: "flex", gap: 8, flexWrap: "wrap", borderTop: "1px solid #1e1e2e", paddingTop: 16 },
  qaBtn: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: 8,
    padding: "8px 14px",
    fontSize: "0.82rem",
    cursor: "pointer",
    transition: "border-color 0.2s",
  },
};
