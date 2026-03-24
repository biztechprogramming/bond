import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  environment: string;
  onSave: () => void;
}

interface Config {
  monitoring_cron: string;
  monitor_health_checks: boolean;
  monitor_logs: boolean;
  monitor_error_rate: boolean;
  monitor_resource_usage: boolean;
  monitor_drift: boolean;
  auto_file_issues: boolean;
  issue_repo: string;
  issue_labels: string;
  issue_dedup_window_hours: number;
}

const DEFAULT_CONFIG: Config = {
  monitoring_cron: "*/15 * * * *",
  monitor_health_checks: true,
  monitor_logs: true,
  monitor_error_rate: true,
  monitor_resource_usage: false,
  monitor_drift: false,
  auto_file_issues: false,
  issue_repo: "",
  issue_labels: "monitoring,auto",
  issue_dedup_window_hours: 24,
};

export default function MonitoringConfig({ environment, onSave }: Props) {
  const [config, setConfig] = useState<Config>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    setLoading(true);
    fetch(`${GATEWAY_API}/deployments/environments/${environment}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (data) {
          setConfig({
            monitoring_cron: data.monitoring_cron || DEFAULT_CONFIG.monitoring_cron,
            monitor_health_checks: data.monitor_health_checks ?? true,
            monitor_logs: data.monitor_logs ?? true,
            monitor_error_rate: data.monitor_error_rate ?? true,
            monitor_resource_usage: data.monitor_resource_usage ?? false,
            monitor_drift: data.monitor_drift ?? false,
            auto_file_issues: data.auto_file_issues ?? false,
            issue_repo: data.issue_repo || "",
            issue_labels: data.issue_labels || "monitoring,auto",
            issue_dedup_window_hours: data.issue_dedup_window_hours ?? 24,
          });
        }
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [environment]);

  const handleSave = async () => {
    setSaving(true);
    setMsg("");
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/monitoring/${environment}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (res.ok) {
        setMsg("Saved.");
        onSave();
      } else {
        const err = await res.json().catch(() => ({ error: "Save failed" }));
        setMsg(`Error: ${err.error || "Save failed"}`);
      }
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
    setSaving(false);
  };

  const set = (key: keyof Config, value: any) => setConfig((c) => ({ ...c, [key]: value }));

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading config...</div>;

  return (
    <div style={styles.container}>
      <div style={styles.card}>
        <span style={styles.cardTitle}>Monitoring Configuration</span>

        <label style={styles.label}>
          Schedule (cron)
          <input style={styles.input} value={config.monitoring_cron} onChange={(e) => set("monitoring_cron", e.target.value)} />
        </label>

        <span style={{ ...styles.label, marginTop: 8 }}>Checks</span>
        <div style={styles.checkGrid}>
          {(["monitor_health_checks", "monitor_logs", "monitor_error_rate", "monitor_resource_usage", "monitor_drift"] as const).map((key) => (
            <label key={key} style={styles.checkLabel}>
              <input type="checkbox" checked={config[key]} onChange={(e) => set(key, e.target.checked)} style={{ accentColor: "#6cffa0" }} />
              {key.replace(/^monitor_/, "").replace(/_/g, " ")}
            </label>
          ))}
        </div>

        <span style={{ ...styles.label, marginTop: 8 }}>GitHub Issue Filing</span>
        <label style={styles.checkLabel}>
          <input type="checkbox" checked={config.auto_file_issues} onChange={(e) => set("auto_file_issues", e.target.checked)} style={{ accentColor: "#6cffa0" }} />
          Auto-file issues
        </label>

        <label style={styles.label}>
          Issue repo (org/repo)
          <input style={styles.input} value={config.issue_repo} onChange={(e) => set("issue_repo", e.target.value)} placeholder="org/repo" />
        </label>

        <label style={styles.label}>
          Labels (comma-separated)
          <input style={styles.input} value={config.issue_labels} onChange={(e) => set("issue_labels", e.target.value)} />
        </label>

        <label style={styles.label}>
          Dedup window (hours)
          <input type="number" style={{ ...styles.input, width: 120 }} value={config.issue_dedup_window_hours} onChange={(e) => set("issue_dedup_window_hours", parseInt(e.target.value) || 0)} min={0} />
        </label>

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 8 }}>
          <button style={styles.button} onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save Configuration"}
          </button>
          {msg && <span style={{ fontSize: "0.8rem", color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{msg}</span>}
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  card: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 8,
  },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  label: { display: "flex", flexDirection: "column", gap: 4, fontSize: "0.8rem", color: "#8888a0" },
  input: {
    backgroundColor: "#16162a",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a5a",
    borderRadius: 6,
    padding: "8px 12px",
    fontSize: "0.85rem",
    width: "100%",
  },
  checkGrid: { display: "flex", flexWrap: "wrap", gap: 12 },
  checkLabel: { display: "flex", alignItems: "center", gap: 4, fontSize: "0.8rem", color: "#e0e0e8", cursor: "pointer" },
  button: {
    backgroundColor: "#6cffa0",
    color: "#0a0a1a",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: 6,
    padding: "8px 16px",
    cursor: "pointer",
    fontWeight: 600,
    fontSize: "0.85rem",
  },
};
