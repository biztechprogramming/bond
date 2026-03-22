import React, { useEffect, useState, useRef } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  environment: string;
}

interface AlertItem {
  id: string;
  severity: string;
  title: string;
  message: string;
  component: string;
  detected_at: string;
  resolved: boolean;
  issue_number: number;
  issue_url: string;
}

type Filter = "all" | "critical" | "high" | "medium" | "low";

const SEV_COLORS: Record<string, string> = {
  critical: "#ff6c8a",
  high: "#ffcc6c",
  medium: "#ffcc6c",
  low: "#6c8aff",
};

export default function IssueTracker({ environment }: Props) {
  const [alerts, setAlerts] = useState<AlertItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<Filter>("all");
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchAlerts = async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/monitoring/${environment}/alerts`);
      if (res.ok) setAlerts(await res.json());
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => {
    fetchAlerts();
    intervalRef.current = setInterval(fetchAlerts, 30000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [environment]);

  const filtered = filter === "all" ? alerts : alerts.filter((a) => a.severity === filter);

  const sevIcon = (sev: string) => {
    if (sev === "critical") return "●";
    if (sev === "high") return "▲";
    if (sev === "medium") return "◆";
    return "○";
  };

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading issues...</div>;

  return (
    <div style={styles.container}>
      {/* Filter buttons */}
      <div style={{ display: "flex", gap: 4, flexWrap: "wrap" as const }}>
        {(["all", "critical", "high", "medium", "low"] as Filter[]).map((f) => (
          <button
            key={f}
            style={filter === f ? styles.activeFilter : styles.filterButton}
            onClick={() => setFilter(f)}
          >
            {f.charAt(0).toUpperCase() + f.slice(1)}
          </button>
        ))}
      </div>

      {/* Table */}
      {filtered.length === 0 ? (
        <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>No issues found.</div>
      ) : (
        <div style={styles.card}>
          {/* Header */}
          <div style={styles.tableHeader}>
            <span style={{ width: 30 }}></span>
            <span style={{ flex: 2 }}>Title</span>
            <span style={{ flex: 1 }}>Component</span>
            <span style={{ flex: 1 }}>Detected</span>
            <span style={{ width: 80 }}>Issue</span>
          </div>

          {filtered.map((alert, i) => {
            const color = SEV_COLORS[alert.severity] || "#8888a0";
            return (
              <div
                key={alert.id || i}
                style={{
                  ...styles.tableRow,
                  opacity: alert.resolved ? 0.5 : 1,
                  textDecoration: alert.resolved ? "line-through" : "none",
                }}
              >
                <span style={{ width: 30, color, fontWeight: 600, fontSize: "0.85rem" }}>{sevIcon(alert.severity)}</span>
                <span style={{ flex: 2, fontSize: "0.8rem", color: "#e0e0e8" }}>{alert.title || alert.message}</span>
                <span style={{ flex: 1, fontSize: "0.75rem", color: "#8888a0" }}>{alert.component || "—"}</span>
                <span style={{ flex: 1, fontSize: "0.75rem", color: "#8888a0" }}>
                  {alert.detected_at ? new Date(alert.detected_at).toLocaleString() : "—"}
                </span>
                <span style={{ width: 80 }}>
                  {alert.issue_number > 0 ? (
                    <a href={alert.issue_url} target="_blank" rel="noreferrer" style={{ fontSize: "0.75rem", color: "#6c8aff" }}>
                      #{alert.issue_number}
                    </a>
                  ) : (
                    <span style={{ fontSize: "0.75rem", color: "#8888a0" }}>—</span>
                  )}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  card: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 12,
    padding: 12,
    display: "flex",
    flexDirection: "column",
    gap: 0,
  },
  filterButton: {
    backgroundColor: "#12121a",
    color: "#8888a0",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 6,
    padding: "4px 10px",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  activeFilter: {
    backgroundColor: "#2a2a4a",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#6c8aff",
    borderRadius: 6,
    padding: "4px 10px",
    fontSize: "0.75rem",
    cursor: "pointer",
    fontWeight: 600,
  },
  tableHeader: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "6px 4px",
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
    fontSize: "0.7rem",
    fontWeight: 600,
    color: "#8888a0",
    textTransform: "uppercase" as const,
  },
  tableRow: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "8px 4px",
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
  },
};
