import React, { useEffect, useState } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

interface Props {
  resourceName: string;
  onBack: () => void;
}

interface Manifest {
  application: string;
  discovered_at: string;
  discovered_by: string;
  layers: Record<string, any>;
  security_observations?: Array<{ severity: string; message: string; detail?: string }>;
}

const LAYER_ORDER = ["system", "web_server", "application", "data_stores", "dns", "topology"];
const LAYER_LABELS: Record<string, string> = {
  system: "System",
  web_server: "Web Server",
  application: "Application",
  data_stores: "Data Stores",
  dns: "DNS",
  topology: "Topology",
};

export default function DiscoveryView({ resourceName, onBack }: Props) {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [discovering, setDiscovering] = useState(false);
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [msg, setMsg] = useState("");

  const fetchManifest = async () => {
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/discovery/manifests/${resourceName}`);
      if (res.ok) setManifest(await res.json());
      else setManifest(null);
    } catch { setManifest(null); }
    setLoading(false);
  };

  useEffect(() => { fetchManifest(); }, [resourceName]);

  const toggleSection = (key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const handleRediscover = async () => {
    setDiscovering(true);
    setMsg("");
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/discovery/manifests/${resourceName}`, { method: "POST" });
      if (res.ok) {
        setManifest(await res.json());
        setMsg("Discovery complete.");
      } else {
        const err = await res.json().catch(() => ({ error: "Discovery failed" }));
        setMsg(`Error: ${err.error || "Discovery failed"}`);
      }
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
    setDiscovering(false);
  };

  const sevColor = (sev: string) => {
    if (sev === "critical") return "#ff6c8a";
    if (sev === "warning") return "#ffcc6c";
    return "#6c8aff";
  };

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading manifest...</div>;

  return (
    <div style={styles.container}>
      <div style={styles.headerRow}>
        <h2 style={styles.title}>{manifest?.application || resourceName}</h2>
        <div style={{ display: "flex", gap: 8 }}>
          <button style={styles.button} onClick={handleRediscover} disabled={discovering}>
            {discovering ? "Discovering..." : "Re-discover"}
          </button>
          <button style={styles.secondaryButton} onClick={onBack}>Back</button>
        </div>
      </div>

      {manifest && (
        <div style={styles.card}>
          <div style={styles.statGrid}>
            <div style={styles.statItem}><span style={styles.statLabel}>Discovered At</span><span style={styles.statValue}>{new Date(manifest.discovered_at).toLocaleString()}</span></div>
            <div style={styles.statItem}><span style={styles.statLabel}>Discovered By</span><span style={styles.statValue}>{manifest.discovered_by}</span></div>
          </div>
        </div>
      )}

      {manifest?.layers && LAYER_ORDER.map((key) => {
        const data = manifest.layers[key];
        if (!data) return null;
        const isOpen = expanded.has(key);
        return (
          <div key={key} style={styles.card}>
            <div style={styles.sectionHeader} onClick={() => toggleSection(key)}>
              <span style={styles.cardTitle}>{LAYER_LABELS[key] || key}</span>
              <span style={{ color: "#8888a0", fontSize: "0.8rem" }}>{isOpen ? "▾" : "▸"}</span>
            </div>
            {isOpen && (
              <pre style={styles.codeBlock}>{JSON.stringify(data, null, 2)}</pre>
            )}
          </div>
        );
      })}

      {manifest?.security_observations && manifest.security_observations.length > 0 && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Security Observations</span>
          {manifest.security_observations.map((obs, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0" }}>
              <span style={{ fontSize: "0.7rem", fontWeight: 600, color: sevColor(obs.severity), backgroundColor: sevColor(obs.severity) + "22", padding: "2px 8px", borderRadius: 4, textTransform: "uppercase" as const }}>{obs.severity}</span>
              <span style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>{obs.message}</span>
            </div>
          ))}
        </div>
      )}

      {!manifest && (
        <div style={styles.card}>
          <span style={{ fontSize: "0.85rem", color: "#8888a0" }}>No manifest found. Click Re-discover to scan this resource.</span>
        </div>
      )}

      {msg && <div style={{ fontSize: "0.85rem", color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  card: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  sectionHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" },
  statGrid: { display: "flex", gap: 16, flexWrap: "wrap" },
  statItem: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: "0.7rem", color: "#8888a0" },
  statValue: { fontSize: "0.85rem", color: "#e0e0e8" },
  codeBlock: {
    backgroundColor: "#0a0a12",
    color: "#e0e0e8",
    padding: 12,
    borderRadius: 8,
    fontSize: "0.75rem",
    overflow: "auto",
    maxHeight: 300,
    margin: 0,
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
  },
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
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: 8,
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
};
