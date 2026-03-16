import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  resourceId: string;
  onBack: () => void;
}

export default function ResourceDetail({ resourceId, onBack }: Props) {
  const [resource, setResource] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [probing, setProbing] = useState(false);
  const [msg, setMsg] = useState("");

  const fetchResource = async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/resources/${resourceId}`);
      if (res.ok) setResource(await res.json());
    } catch { /* ignore */ }
    setLoading(false);
  };

  useEffect(() => { fetchResource(); }, [resourceId]);

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading...</div>;
  if (!resource) return <div style={{ color: "#ff6c8a", fontSize: "0.85rem" }}>Resource not found.</div>;

  const state = JSON.parse(resource.state_json || "{}");
  const capabilities = JSON.parse(resource.capabilities_json || "{}");
  const recommendations: any[] = JSON.parse(resource.recommendations_json || "[]");
  const statusColor = state.status === "online" ? "#6cffa0" : state.status === "pending" ? "#ffcc6c" : "#8888a0";

  const handleProbe = async () => {
    setProbing(true);
    setMsg("");
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/resources/${resourceId}/probe`, { method: "POST" });
      if (res.ok) {
        setResource(await res.json());
        setMsg("Probe complete.");
      } else {
        const err = await res.json();
        setMsg(`Probe failed: ${err.error}`);
      }
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
    setProbing(false);
  };

  const handleApply = async (rank: number) => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/resources/${resourceId}/recommendations/${rank}/apply`, { method: "POST" });
      const data = await res.json();
      setMsg(data.message);
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.headerRow}>
        <h2 style={styles.title}>{resource.display_name || resource.name}</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={styles.secondaryButton} onClick={handleProbe} disabled={probing}>
            {probing ? "Probing..." : "Re-probe"}
          </button>
          <button style={styles.secondaryButton} onClick={onBack}>Back</button>
        </div>
      </div>

      {/* Status */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Status</span>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ width: 10, height: 10, borderRadius: "50%", backgroundColor: statusColor }} />
          <span style={{ fontSize: "0.9rem", color: "#e0e0e8", textTransform: "capitalize" as const }}>{state.status || "unknown"}</span>
        </div>
        <div style={styles.statGrid}>
          {state.cpus && <div style={styles.statItem}><span style={styles.statLabel}>CPUs</span><span style={styles.statValue}>{state.cpus}</span></div>}
          {state.memory_gb !== undefined && <div style={styles.statItem}><span style={styles.statLabel}>RAM</span><span style={styles.statValue}>{state.memory_gb}GB</span></div>}
          {state.disk_available_gb && <div style={styles.statItem}><span style={styles.statLabel}>Disk Free</span><span style={styles.statValue}>{state.disk_available_gb}</span></div>}
          {state.os && <div style={styles.statItem}><span style={styles.statLabel}>OS</span><span style={styles.statValue}>{state.os}</span></div>}
          {state.hostname && <div style={styles.statItem}><span style={styles.statLabel}>Hostname</span><span style={styles.statValue}>{state.hostname}</span></div>}
          {state.uptime && <div style={styles.statItem}><span style={styles.statLabel}>Uptime</span><span style={styles.statValue}>{state.uptime}</span></div>}
        </div>
      </div>

      {/* Capabilities */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Capabilities</span>
        <div style={{ display: "flex", flexDirection: "column" as const, gap: 4 }}>
          {Object.entries(capabilities).filter(([k, v]) => v && v !== "unknown" && k !== "local" && k !== "note").map(([k, v]) => (
            <div key={k} style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>
              <span style={{ color: "#8888a0" }}>{k}:</span> {String(v)}
            </div>
          ))}
          {capabilities.note && (
            <div style={{ fontSize: "0.8rem", color: "#ffcc6c", marginTop: 4 }}>{capabilities.note}</div>
          )}
        </div>
      </div>

      {/* Recommendations */}
      {recommendations.length > 0 && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Recommendations</span>
          {recommendations.map((rec: any) => (
            <div key={rec.rank} style={styles.recItem}>
              <div style={{ flex: 1 }}>
                <span style={{
                  fontSize: "0.85rem",
                  fontWeight: 600,
                  color: rec.severity === "high" ? "#ff6c8a" : rec.severity === "medium" ? "#ffcc6c" : "#e0e0e8",
                }}>{rec.title}</span>
                <div style={{ fontSize: "0.8rem", color: "#8888a0", marginTop: 2 }}>{rec.description}</div>
              </div>
              <button style={styles.applyButton} onClick={() => handleApply(rec.rank)}>Apply</button>
            </div>
          ))}
        </div>
      )}

      {/* Deployment history stub */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Deployment History</span>
        <span style={{ fontSize: "0.8rem", color: "#8888a0" }}>No deployments to this resource yet.</span>
      </div>

      {msg && (
        <div style={{ fontSize: "0.85rem", color: msg.includes("failed") || msg.includes("Error") ? "#ff6c8a" : "#6cffa0", marginTop: 4 }}>
          {msg}
        </div>
      )}

      <div style={{ fontSize: "0.75rem", color: "#555", marginTop: 8 }}>
        Last probed: {resource.last_probed_at ? new Date(resource.last_probed_at).toLocaleString() : "Never"}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 16 },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  card: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  statGrid: { display: "flex", gap: 16, flexWrap: "wrap" },
  statItem: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: "0.7rem", color: "#8888a0" },
  statValue: { fontSize: "0.85rem", color: "#e0e0e8" },
  recItem: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "8px 0",
    borderBottom: "1px solid #1e1e2e",
  },
  applyButton: {
    backgroundColor: "#2a4a2a",
    color: "#6cffa0",
    border: "1px solid #3a5a3a",
    borderRadius: 6,
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
    flexShrink: 0,
  },
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: 8,
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
};
