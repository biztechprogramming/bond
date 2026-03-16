import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface HistoryEntry {
  id: string;
  environment_name: string;
  action: string;
  changed_by: string;
  changed_at: number;
  before_snapshot: string;
  after_snapshot: string;
}

interface DiffField {
  field: string;
  before: any;
  after: any;
}

function relativeTime(epochMs: number): string {
  const diff = Math.floor((Date.now() - epochMs) / 1000);
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function computeDiff(before: string, after: string): DiffField[] {
  try {
    const b = before ? JSON.parse(before) : {};
    const a = after ? JSON.parse(after) : {};
    const allKeys = new Set([...Object.keys(b), ...Object.keys(a)]);
    const diffs: DiffField[] = [];
    for (const key of allKeys) {
      if (JSON.stringify(b[key]) !== JSON.stringify(a[key])) {
        diffs.push({ field: key, before: b[key], after: a[key] });
      }
    }
    return diffs;
  } catch {
    return [];
  }
}

function actionLabel(action: string): string {
  switch (action) {
    case "created": return "Created";
    case "updated": return "Updated";
    case "deactivated": return "Deactivated";
    case "approver_added": return "Approver Added";
    case "approver_removed": return "Approver Removed";
    default: return action;
  }
}

function actionColor(action: string): string {
  switch (action) {
    case "created": return "#4caf50";
    case "updated": return "#6c8aff";
    case "deactivated": return "#ff6c8a";
    case "approver_added": return "#4caf50";
    case "approver_removed": return "#ff9800";
    default: return "#8888a0";
  }
}

interface Props {
  environmentName: string;
}

export default function EnvironmentHistory({ environmentName }: Props) {
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(
          `${GATEWAY_API}/deployments/environments/${encodeURIComponent(environmentName)}/history`
        );
        if (res.ok) {
          setEntries(await res.json());
        } else if (res.status !== 404) {
          setError("Failed to load history.");
        }
      } catch {
        // API may not exist yet
      }
      setLoaded(true);
    })();
  }, [environmentName]);

  if (!loaded) return null;

  if (entries.length === 0) {
    return (
      <div style={styles.section}>
        <h4 style={styles.title}>History</h4>
        <p style={styles.empty}>No history entries yet.</p>
        {error && <p style={styles.errorText}>{error}</p>}
      </div>
    );
  }

  return (
    <div style={styles.section}>
      <h4 style={styles.title}>History</h4>
      <div style={styles.list}>
        {entries.map((entry) => {
          const isExpanded = expandedId === entry.id;
          const diffs = computeDiff(entry.before_snapshot, entry.after_snapshot);

          return (
            <div key={entry.id}>
              <div
                style={{
                  ...styles.row,
                  borderColor: isExpanded ? "#6c8aff" : "#1e1e2e",
                }}
                onClick={() => setExpandedId(isExpanded ? null : entry.id)}
              >
                <span
                  style={{
                    ...styles.action,
                    color: actionColor(entry.action),
                  }}
                >
                  {actionLabel(entry.action)}
                </span>
                <span style={styles.changedBy}>{entry.changed_by}</span>
                <span style={styles.time}>{relativeTime(entry.changed_at)}</span>
              </div>

              {isExpanded && diffs.length > 0 && (
                <div style={styles.diffContainer}>
                  <table style={styles.diffTable}>
                    <thead>
                      <tr>
                        <th style={styles.diffHeader}>Field</th>
                        <th style={styles.diffHeader}>Before</th>
                        <th style={styles.diffHeader}>After</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diffs.map((d) => (
                        <tr key={d.field}>
                          <td style={styles.diffCell}>{d.field}</td>
                          <td style={{ ...styles.diffCell, color: "#ff6c8a" }}>
                            {d.before !== undefined ? String(d.before) : "—"}
                          </td>
                          <td style={{ ...styles.diffCell, color: "#4caf50" }}>
                            {d.after !== undefined ? String(d.after) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              {isExpanded && diffs.length === 0 && (
                <div style={styles.diffContainer}>
                  <p style={styles.empty}>No field changes recorded.</p>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  section: { display: "flex", flexDirection: "column", gap: "8px" },
  title: { fontSize: "0.9rem", fontWeight: 600, color: "#8888a0", margin: "0 0 4px 0" },
  empty: { fontSize: "0.85rem", color: "#5a5a6e", margin: 0 },
  errorText: { fontSize: "0.8rem", color: "#ff6c8a", margin: 0 },
  list: { display: "flex", flexDirection: "column", gap: "2px" },
  row: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    padding: "10px 12px",
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "6px",
    cursor: "pointer",
    transition: "border-color 0.2s",
    fontSize: "0.8rem",
  },
  action: { fontWeight: 600, minWidth: "120px" },
  changedBy: { color: "#e0e0e8", flex: 1 },
  time: { color: "#8888a0", fontSize: "0.75rem", flexShrink: 0 },
  diffContainer: {
    padding: "8px 12px 8px 24px",
    borderLeft: "2px solid #1e1e2e",
    marginLeft: "16px",
    marginTop: "4px",
    marginBottom: "8px",
  },
  diffTable: {
    width: "100%",
    borderCollapse: "collapse" as const,
    fontSize: "0.75rem",
  },
  diffHeader: {
    textAlign: "left" as const,
    padding: "4px 8px",
    color: "#8888a0",
    borderBottom: "1px solid #1e1e2e",
    fontWeight: 500,
  },
  diffCell: {
    padding: "4px 8px",
    color: "#e0e0e8",
    borderBottom: "1px solid #0a0a12",
    fontFamily: "monospace",
    fontSize: "0.7rem",
  },
};
