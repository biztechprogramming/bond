import React, { useEffect, useState, useCallback } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

interface CompareEnvironmentsProps {
  environments: Array<{ name: string; display_name: string }>;
  onBack: () => void;
}

interface ComparisonRow {
  key: string;
  category: string;
  envA_value: string;
  envB_value: string;
  is_same: boolean;
}

interface ComparisonData {
  software_versions: ComparisonRow[];
  script_versions: ComparisonRow[];
  configuration: ComparisonRow[];
  server_resources: ComparisonRow[];
  can_promote: boolean;
}

interface Component {
  id: string;
  name: string;
  display_name: string;
  component_type: string;
  icon: string | null;
  is_active: boolean;
}

interface ComponentComparison {
  name: string;
  display_name: string;
  envA: { present: boolean; resource?: string; version?: string } | null;
  envB: { present: boolean; resource?: string; version?: string } | null;
}

const SECTIONS = ["software_versions", "script_versions", "configuration", "server_resources"] as const;
const SECTION_LABELS: Record<string, string> = {
  software_versions: "Software Versions",
  script_versions: "Script Versions",
  configuration: "Configuration",
  server_resources: "Server Resources",
};

export default function CompareEnvironments({ environments, onBack }: CompareEnvironmentsProps) {
  const [envA, setEnvA] = useState(environments[0]?.name || "");
  const [envB, setEnvB] = useState(environments[environments.length - 1]?.name || "");
  const [data, setData] = useState<ComparisonData | null>(null);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [componentComparisons, setComponentComparisons] = useState<ComponentComparison[]>([]);

  const fetchComparison = useCallback(async () => {
    if (!envA || !envB || envA === envB) { setData(null); return; }
    setLoading(true);
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/compare/${envA}/${envB}`);
      if (res.ok) setData(await res.json());
      else setMsg("Failed to load comparison");
    } catch { setMsg("Failed to load comparison"); }
    setLoading(false);
  }, [envA, envB]);

  useEffect(() => { fetchComparison(); }, [fetchComparison]);

  // Fetch components for both environments
  useEffect(() => {
    if (!envA || !envB || envA === envB) { setComponentComparisons([]); return; }
    Promise.all([
      apiFetch(`${GATEWAY_API}/deployments/components?environment=${encodeURIComponent(envA)}`).then(r => r.ok ? r.json() : []).catch(() => []),
      apiFetch(`${GATEWAY_API}/deployments/components?environment=${encodeURIComponent(envB)}`).then(r => r.ok ? r.json() : []).catch(() => []),
    ]).then(([aData, bData]) => {
      const compsA: Component[] = Array.isArray(aData) ? aData : aData.components || [];
      const compsB: Component[] = Array.isArray(bData) ? bData : bData.components || [];
      const allNames = new Set([...compsA.map(c => c.name), ...compsB.map(c => c.name)]);
      const comparisons: ComponentComparison[] = [];
      for (const name of allNames) {
        const a = compsA.find(c => c.name === name);
        const b = compsB.find(c => c.name === name);
        comparisons.push({
          name,
          display_name: a?.display_name || b?.display_name || name,
          envA: a ? { present: true, resource: a.component_type } : null,
          envB: b ? { present: true, resource: b.component_type } : null,
        });
      }
      setComponentComparisons(comparisons);
    });
  }, [envA, envB]);

  const promote = async () => {
    if (!confirm(`Promote all scripts from ${envA} to ${envB}?`)) return;
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/compare/${envA}/${envB}/promote`, { method: "POST" });
      setMsg(res.ok ? "Promotion initiated" : "Promotion failed");
      if (res.ok) await fetchComparison();
    } catch { setMsg("Promotion failed"); }
  };

  const envDisplay = (name: string) => environments.find((e) => e.name === name)?.display_name || name;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>Compare Environments</h3>
        <button style={styles.secondaryBtn} onClick={onBack}>Back</button>
      </div>

      {/* Environment selectors */}
      <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
        <select style={styles.select} value={envA} onChange={(e) => setEnvA(e.target.value)}>
          {environments.map((env) => <option key={env.name} value={env.name}>{env.display_name}</option>)}
        </select>
        <span style={{ color: "#8888a0", fontSize: "0.9rem" }}>vs</span>
        <select style={styles.select} value={envB} onChange={(e) => setEnvB(e.target.value)}>
          {environments.map((env) => <option key={env.name} value={env.name}>{env.display_name}</option>)}
        </select>
        {data?.can_promote && (
          <button style={styles.primaryBtn} onClick={promote}>
            Promote All to {envDisplay(envB)}
          </button>
        )}
      </div>

      {envA === envB && <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Select two different environments to compare.</div>}
      {loading && <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading comparison...</div>}

      {/* Components comparison */}
      {componentComparisons.length > 0 && (
        <div style={{ backgroundColor: "#1a1a2e", borderRadius: "8px", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e", overflow: "hidden" }}>
          <div style={{ padding: "10px 14px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#3a3a4e", fontWeight: 600, color: "#e0e0e8", fontSize: "0.9rem" }}>
            Components
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "200px 1fr 1fr 60px", padding: "6px 14px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#2a2a3e" }}>
            <span style={styles.header}>Component</span>
            <span style={styles.header}>{envDisplay(envA)}</span>
            <span style={styles.header}>{envDisplay(envB)}</span>
            <span style={styles.header}>Status</span>
          </div>
          {componentComparisons.map((cc) => {
            const bothPresent = cc.envA && cc.envB;
            const same = bothPresent;
            const missing = !cc.envA || !cc.envB;
            return (
              <div key={cc.name} style={{ display: "grid", gridTemplateColumns: "200px 1fr 1fr 60px", padding: "6px 14px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e32", alignItems: "center" }}>
                <span style={{ color: "#e0e0e8", fontSize: "0.85rem" }}>{cc.display_name}</span>
                <span style={{ color: "#8888a0", fontSize: "0.85rem", fontFamily: "monospace" }}>{cc.envA ? cc.envA.resource || "present" : "—"}</span>
                <span style={{ color: "#8888a0", fontSize: "0.85rem", fontFamily: "monospace" }}>{cc.envB ? cc.envB.resource || "present" : "—"}</span>
                <span style={{ fontSize: "0.85rem", color: missing ? "#ff6c8a" : same ? "#6cffa0" : "#ffcc6c" }}>
                  {missing ? "✗" : same ? "✓" : "⚠"}
                </span>
              </div>
            );
          })}
        </div>
      )}

      {/* Comparison sections */}
      {data && SECTIONS.map((section) => {
        const rows = data[section] || [];
        if (rows.length === 0) return null;
        return (
          <div key={section} style={{ backgroundColor: "#1a1a2e", borderRadius: "8px", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e", overflow: "hidden" }}>
            <div style={{ padding: "10px 14px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#3a3a4e", fontWeight: 600, color: "#e0e0e8", fontSize: "0.9rem" }}>
              {SECTION_LABELS[section]}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "200px 1fr 1fr 60px", padding: "6px 14px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#2a2a3e" }}>
              <span style={styles.header}>Item</span>
              <span style={styles.header}>{envDisplay(envA)}</span>
              <span style={styles.header}>{envDisplay(envB)}</span>
              <span style={styles.header}>Status</span>
            </div>
            {rows.map((row) => (
              <div key={row.key} style={{ display: "grid", gridTemplateColumns: "200px 1fr 1fr 60px", padding: "6px 14px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e32", alignItems: "center" }}>
                <span style={{ color: "#e0e0e8", fontSize: "0.85rem" }}>{row.key}</span>
                <span style={{ color: "#8888a0", fontSize: "0.85rem", fontFamily: "monospace" }}>{row.envA_value || "—"}</span>
                <span style={{ color: "#8888a0", fontSize: "0.85rem", fontFamily: "monospace" }}>{row.envB_value || "—"}</span>
                <span style={{ fontSize: "0.85rem", color: row.is_same ? "#6cffa0" : "#ffcc6c" }}>
                  {row.is_same ? "✓" : "⚠"}
                </span>
              </div>
            ))}
          </div>
        );
      })}

      {msg && <div style={{ fontSize: "0.85rem", color: msg.includes("fail") || msg.includes("Failed") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  primaryBtn: { backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" },
  secondaryBtn: { backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" },
  select: { backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e", borderRadius: "6px", padding: "8px 12px", fontSize: "0.85rem", minWidth: "160px" },
  header: { color: "#8888a0", fontSize: "0.75rem", fontWeight: 600, textTransform: "uppercase" as const },
};
