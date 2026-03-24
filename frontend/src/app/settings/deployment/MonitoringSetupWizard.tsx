import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  environment: string;
  onComplete: () => void;
  onCancel: () => void;
}

interface DiscoveredService {
  id: string;
  name: string;
  type: string;
  host?: string;
  detectedChecks: string[];
}

interface ServiceMonitorConfig {
  enabled: boolean;
  checkType: string;
  intervalSeconds: number;
  expanded: boolean;
  healthUrl: string;
  thresholds: { errorRate: number; responseMs: number };
}

const ENV_DEFAULTS: Record<string, number> = { production: 60, staging: 120, dev: 300, development: 300 };

function defaultInterval(env: string): number {
  return ENV_DEFAULTS[env] || 300;
}

function detectChecks(type: string): string[] {
  if (["app", "application", "app-server", "web_server"].includes(type)) return ["http", "process", "error-rate"];
  if (["postgresql", "mysql", "database"].includes(type)) return ["connection", "replication-lag", "disk"];
  if (["redis", "cache"].includes(type)) return ["ping", "memory"];
  return ["ping"];
}

export default function MonitoringSetupWizard({ environment, onComplete, onCancel }: Props) {
  const [loading, setLoading] = useState(true);
  const [services, setServices] = useState<DiscoveredService[]>([]);
  const [configs, setConfigs] = useState<Record<string, ServiceMonitorConfig>>({});
  const [issueRepo, setIssueRepo] = useState("");
  const [autoFileIssues, setAutoFileIssues] = useState(false);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    setLoading(true);
    fetch(`${GATEWAY_API}/deployments/discovery/manifests`)
      .then((r) => r.ok ? r.json() : [])
      .then((manifests: any[]) => {
        const svcs: DiscoveredService[] = [];
        for (const m of manifests) {
          if (!m.layers) continue;
          for (const [key, val] of Object.entries(m.layers as Record<string, any>)) {
            if (key === "topology" || key === "dns") continue;
            const items = Array.isArray(val) ? val : val ? [val] : [];
            for (const item of items) {
              const type = item.type || key;
              svcs.push({ id: item.id || item.name || `${key}-${svcs.length}`, name: item.name || key, type, host: item.host, detectedChecks: detectChecks(type) });
            }
          }
        }
        setServices(svcs);
        const cfgs: Record<string, ServiceMonitorConfig> = {};
        const interval = defaultInterval(environment);
        for (const s of svcs) {
          cfgs[s.id] = { enabled: true, checkType: s.detectedChecks[0] || "ping", intervalSeconds: interval, expanded: false, healthUrl: s.host ? `http://${s.host}/health` : "", thresholds: { errorRate: 5, responseMs: 2000 } };
        }
        setConfigs(cfgs);
      })
      .catch(() => setServices([]))
      .finally(() => setLoading(false));
  }, [environment]);

  const setCfg = (id: string, key: keyof ServiceMonitorConfig, value: any) => {
    setConfigs((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }));
  };

  const handleEnable = async () => {
    setSaving(true);
    setMsg("");
    try {
      const enabled = services.filter((s) => configs[s.id]?.enabled);
      const res = await fetch(`${GATEWAY_API}/deployments/monitoring/${environment}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          services: enabled.map((s) => ({ id: s.id, name: s.name, checkType: configs[s.id].checkType, intervalSeconds: configs[s.id].intervalSeconds, healthUrl: configs[s.id].healthUrl, thresholds: configs[s.id].thresholds })),
          auto_file_issues: autoFileIssues,
          issue_repo: issueRepo,
        }),
      });
      if (res.ok) { setMsg("Monitoring enabled."); onComplete(); }
      else { const err = await res.json().catch(() => ({})); setMsg(`Error: ${err.error || "Failed"}`); }
    } catch (err: any) { setMsg(`Error: ${err.message}`); }
    setSaving(false);
  };

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading services...</div>;

  return (
    <div style={styles.container}>
      <div style={styles.headerRow}>
        <h2 style={styles.title}>Monitoring Setup — {environment}</h2>
        <button style={styles.secondaryButton} onClick={onCancel}>Cancel</button>
      </div>

      {/* Service Table */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Discovered Services</span>
        {services.length === 0 && <span style={{ fontSize: "0.85rem", color: "#8888a0" }}>No services discovered. Run discovery first.</span>}
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {/* Header */}
          {services.length > 0 && (
            <div style={styles.tableHeader}>
              <span style={{ width: 32 }}></span>
              <span style={{ flex: 2 }}>Service</span>
              <span style={{ flex: 1 }}>Type</span>
              <span style={{ flex: 1 }}>Check</span>
              <span style={{ flex: 1 }}>Interval</span>
              <span style={{ width: 40 }}></span>
            </div>
          )}
          {services.map((s) => {
            const cfg = configs[s.id];
            if (!cfg) return null;
            return (
              <div key={s.id}>
                <div style={styles.tableRow}>
                  <span style={{ width: 32 }}>
                    <input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg(s.id, "enabled", e.target.checked)} style={{ accentColor: "#6cffa0" }} />
                  </span>
                  <span style={{ flex: 2, color: "#e0e0e8", fontSize: "0.85rem", fontWeight: 600 }}>{s.name}</span>
                  <span style={{ flex: 1, color: "#8888a0", fontSize: "0.8rem" }}>{s.type}</span>
                  <span style={{ flex: 1 }}>
                    <select style={styles.selectSmall} value={cfg.checkType} onChange={(e) => setCfg(s.id, "checkType", e.target.value)}>
                      {s.detectedChecks.map((c) => <option key={c} value={c}>{c}</option>)}
                    </select>
                  </span>
                  <span style={{ flex: 1 }}>
                    <select style={styles.selectSmall} value={cfg.intervalSeconds} onChange={(e) => setCfg(s.id, "intervalSeconds", Number(e.target.value))}>
                      <option value={30}>30s</option>
                      <option value={60}>1m</option>
                      <option value={120}>2m</option>
                      <option value={300}>5m</option>
                      <option value={600}>10m</option>
                    </select>
                  </span>
                  <span style={{ width: 40, textAlign: "center", cursor: "pointer", color: "#8888a0", fontSize: "0.8rem" }} onClick={() => setCfg(s.id, "expanded", !cfg.expanded)}>
                    {cfg.expanded ? "▾" : "▸"}
                  </span>
                </div>
                {cfg.expanded && (
                  <div style={{ padding: "8px 32px", display: "flex", gap: 12, flexWrap: "wrap", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" }}>
                    <label style={styles.fieldLabel}>
                      Health URL
                      <input style={{ ...styles.input, width: 240 }} value={cfg.healthUrl} onChange={(e) => setCfg(s.id, "healthUrl", e.target.value)} />
                    </label>
                    <label style={styles.fieldLabel}>
                      Error rate threshold (%)
                      <input type="number" style={{ ...styles.input, width: 80 }} value={cfg.thresholds.errorRate} onChange={(e) => setCfg(s.id, "thresholds", { ...cfg.thresholds, errorRate: Number(e.target.value) })} />
                    </label>
                    <label style={styles.fieldLabel}>
                      Response time (ms)
                      <input type="number" style={{ ...styles.input, width: 100 }} value={cfg.thresholds.responseMs} onChange={(e) => setCfg(s.id, "thresholds", { ...cfg.thresholds, responseMs: Number(e.target.value) })} />
                    </label>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </div>

      {/* Issue Filing */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>GitHub Issue Auto-Filing</span>
        <label style={styles.checkLabel}>
          <input type="checkbox" checked={autoFileIssues} onChange={(e) => setAutoFileIssues(e.target.checked)} style={{ accentColor: "#6cffa0" }} />
          Auto-file issues on failures
        </label>
        {autoFileIssues && (
          <label style={styles.fieldLabel}>
            Issue repo (org/repo)
            <input style={{ ...styles.input, width: 280 }} value={issueRepo} onChange={(e) => setIssueRepo(e.target.value)} placeholder="org/repo" />
          </label>
        )}
      </div>

      <div style={{ display: "flex", gap: 8 }}>
        <button style={styles.button} onClick={handleEnable} disabled={saving}>
          {saving ? "Enabling..." : "Enable Monitoring"}
        </button>
      </div>

      {msg && <div style={{ fontSize: "0.85rem", color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  card: { backgroundColor: "#12121a", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 10 },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  tableHeader: { display: "flex", alignItems: "center", gap: 8, padding: "6px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", fontSize: "0.7rem", color: "#8888a0", textTransform: "uppercase" as const },
  tableRow: { display: "flex", alignItems: "center", gap: 8, padding: "8px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#0a0a12" },
  checkLabel: { display: "flex", alignItems: "center", gap: 4, fontSize: "0.8rem", color: "#e0e0e8", cursor: "pointer" },
  fieldLabel: { display: "flex", flexDirection: "column", gap: 2, fontSize: "0.75rem", color: "#8888a0" },
  input: { backgroundColor: "#16162a", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a5a", borderRadius: 6, padding: "6px 10px", fontSize: "0.8rem" },
  selectSmall: { backgroundColor: "#16162a", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a5a", borderRadius: 4, padding: "4px 8px", fontSize: "0.75rem" },
  button: { backgroundColor: "#6cffa0", color: "#0a0a1a", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: 6, padding: "8px 16px", cursor: "pointer", fontWeight: 600, fontSize: "0.85rem" },
  secondaryButton: { backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e", borderRadius: 8, padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" },
};
