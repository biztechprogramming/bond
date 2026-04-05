import React, { useEffect, useState } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

interface Props {
  environment: string;
}

interface Alert {
  id: string;
  severity: string;
  message: string;
  component: string;
  detected_at: string;
  resolved: boolean;
}

interface Issue {
  id: string;
  title: string;
  severity: string;
  component: string;
  issue_number: number;
  issue_url: string;
  detected_at: string;
  status: string;
}

interface MonitoringStatus {
  environment: string;
  schedule: string;
  last_run: string;
  status: string;
  checks_enabled: string[];
  monitored_resources: string[];
  recent_alerts: Alert[];
}

export default function MonitoringSection({ environment }: Props) {
  const [status, setStatus] = useState<MonitoringStatus | null>(null);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchStatus = async () => {
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/monitoring/${environment}`);
      if (res.ok) setStatus(await res.json());
    } catch { /* ignore */ }
  };

  const fetchIssues = async () => {
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/monitoring/${environment}/issues`);
      if (res.ok) setIssues(await res.json());
    } catch { /* ignore */ }
  };

  useEffect(() => {
    setLoading(true);
    Promise.all([fetchStatus(), fetchIssues()]).finally(() => setLoading(false));
  }, [environment]);

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading monitoring...</div>;
  if (!status) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>No monitoring data for {environment}.</div>;

  const statusColor = status.status === "healthy" ? "#6cffa0" : status.status === "warning" ? "#ffcc6c" : status.status === "critical" ? "#ff6c8a" : "#8888a0";

  const severityIcon = (sev: string) => {
    if (sev === "critical" || sev === "error") return "✕";
    if (sev === "warning" || sev === "high") return "⚠";
    return "✓";
  };

  const severityColor = (sev: string) => {
    if (sev === "critical" || sev === "error") return "#ff6c8a";
    if (sev === "warning" || sev === "high") return "#ffcc6c";
    return "#6cffa0";
  };

  return (
    <div style={styles.container}>
      {/* Overview */}
      <div style={styles.card}>
        <div style={styles.headerRow}>
          <span style={styles.cardTitle}>Monitoring Status</span>
          <span style={{ ...styles.dot, backgroundColor: statusColor }} />
        </div>
        <div style={styles.statGrid}>
          <div style={styles.statItem}>
            <span style={styles.statLabel}>Schedule</span>
            <span style={styles.statValue}>{status.schedule || "—"}</span>
          </div>
          <div style={styles.statItem}>
            <span style={styles.statLabel}>Last Run</span>
            <span style={styles.statValue}>{status.last_run ? new Date(status.last_run).toLocaleString() : "Never"}</span>
          </div>
          <div style={styles.statItem}>
            <span style={styles.statLabel}>Status</span>
            <span style={{ ...styles.statValue, color: statusColor, textTransform: "capitalize" as const }}>{status.status}</span>
          </div>
        </div>
      </div>

      {/* Monitored Resources */}
      {status.monitored_resources && status.monitored_resources.length > 0 && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Monitored Resources</span>
          <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 6 }}>
            {status.monitored_resources.map((r) => (
              <span key={r} style={styles.tag}>{r}</span>
            ))}
          </div>
        </div>
      )}

      {/* Active Checks */}
      {status.checks_enabled && status.checks_enabled.length > 0 && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Active Checks</span>
          <div style={{ display: "flex", flexWrap: "wrap" as const, gap: 8 }}>
            {status.checks_enabled.map((c) => (
              <label key={c} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: "0.8rem", color: "#e0e0e8" }}>
                <input type="checkbox" checked readOnly style={{ accentColor: "#6cffa0" }} />
                {c.replace(/_/g, " ")}
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Recent Alerts */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Recent Alerts</span>
        {(!status.recent_alerts || status.recent_alerts.length === 0) ? (
          <span style={{ fontSize: "0.8rem", color: "#8888a0" }}>No recent alerts.</span>
        ) : (
          <div style={{ display: "flex", flexDirection: "column" as const, gap: 4 }}>
            {status.recent_alerts.slice(0, 10).map((alert, i) => (
              <div key={alert.id || i} style={styles.alertRow}>
                <span style={{ color: severityColor(alert.severity), fontWeight: 600, fontSize: "0.85rem", width: 16 }}>{severityIcon(alert.severity)}</span>
                <span style={{ fontSize: "0.75rem", color: "#8888a0", minWidth: 130 }}>{new Date(alert.detected_at).toLocaleString()}</span>
                <span style={{ fontSize: "0.8rem", color: "#e0e0e8", flex: 1 }}>{alert.message}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Open Issues */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Open Issues</span>
        {issues.length === 0 ? (
          <span style={{ fontSize: "0.8rem", color: "#8888a0" }}>No open issues.</span>
        ) : (
          <div style={{ display: "flex", flexDirection: "column" as const, gap: 4 }}>
            {issues.map((issue, i) => (
              <div key={issue.id || i} style={styles.alertRow}>
                <span style={{ color: severityColor(issue.severity), fontWeight: 600, fontSize: "0.85rem", width: 16 }}>{severityIcon(issue.severity)}</span>
                <span style={{ fontSize: "0.8rem", color: "#e0e0e8", flex: 1 }}>{issue.title}</span>
                {issue.issue_number > 0 && (
                  <a href={issue.issue_url} target="_blank" rel="noreferrer" style={{ fontSize: "0.75rem", color: "#6c8aff" }}>#{issue.issue_number}</a>
                )}
              </div>
            ))}
          </div>
        )}
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
    gap: 10,
  },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  dot: { width: 10, height: 10, borderRadius: "50%" },
  statGrid: { display: "flex", gap: 16, flexWrap: "wrap" },
  statItem: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: "0.7rem", color: "#8888a0" },
  statValue: { fontSize: "0.85rem", color: "#e0e0e8" },
  tag: { fontSize: "0.75rem", color: "#e0e0e8", backgroundColor: "#0a0a12", padding: "2px 8px", borderRadius: 4 },
  alertRow: { display: "flex", alignItems: "center", gap: 8, padding: "4px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" },
};
