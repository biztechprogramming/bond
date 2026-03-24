import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface RegisteredScript {
  name: string;
  description: string;
  content: string;
  level: number;
}

interface ScriptFromDiscoveryWizardProps {
  manifestName: string;
  environment: string;
  onComplete: (scripts: RegisteredScript[]) => void;
  onCancel: () => void;
  componentId?: string;
  componentName?: string;
}

interface DiscoveredComponent {
  id: string;
  name: string;
  type: string; // "app" | "data_store" | "cache" | "web_server" | etc
  host?: string;
  port?: number;
}

interface ComponentOptions {
  enabled: boolean;
  restartMethod: string;
  healthCheckUrl: string;
  backup: boolean;
}

const LEVELS = [
  { level: 0, label: "Level 0 — Replication" },
  { level: 1, label: "Level 1 — Operational" },
  { level: 2, label: "Level 2 — Architecture" },
];

const RESTART_METHODS = ["rolling", "blue-green", "in-place", "none"];

function isAppType(type: string): boolean {
  return ["app", "application", "app-server", "web_server"].includes(type);
}

export default function ScriptFromDiscoveryWizard({ manifestName, environment, onComplete, onCancel, componentId, componentName }: ScriptFromDiscoveryWizardProps) {
  const [loading, setLoading] = useState(true);
  const [components, setComponents] = useState<DiscoveredComponent[]>([]);
  const [options, setOptions] = useState<Record<string, ComponentOptions>>({});
  const [level, setLevel] = useState(0);
  const [preview, setPreview] = useState<string | null>(null);
  const [generating, setGenerating] = useState(false);
  const [msg, setMsg] = useState("");

  useEffect(() => {
    setLoading(true);
    fetch(`${GATEWAY_API}/deployments/discovery/manifests/${manifestName}`)
      .then((r) => r.ok ? r.json() : null)
      .then((data) => {
        if (!data?.layers) { setComponents([]); return; }
        const comps: DiscoveredComponent[] = [];
        for (const [layerKey, layerData] of Object.entries(data.layers as Record<string, any>)) {
          if (layerKey === "topology" || layerKey === "dns") continue;
          if (Array.isArray(layerData)) {
            for (const item of layerData) {
              comps.push({ id: item.id || item.name || `${layerKey}-${comps.length}`, name: item.name || layerKey, type: item.type || layerKey, host: item.host, port: item.port });
            }
          } else if (layerData && typeof layerData === "object") {
            comps.push({ id: layerData.id || layerData.name || layerKey, name: layerData.name || layerKey, type: layerData.type || layerKey, host: layerData.host, port: layerData.port });
          }
        }
        setComponents(comps);
        const opts: Record<string, ComponentOptions> = {};
        for (const c of comps) {
          // If scoped to a component, enable all by default; otherwise use type heuristic
          const enabled = componentId ? true : isAppType(c.type);
          opts[c.id] = { enabled, restartMethod: "rolling", healthCheckUrl: c.host ? `http://${c.host}${c.port ? `:${c.port}` : ""}/health` : "", backup: !isAppType(c.type) };
        }
        setOptions(opts);
      })
      .catch(() => setComponents([]))
      .finally(() => setLoading(false));
  }, [manifestName]);

  const setOpt = (id: string, key: keyof ComponentOptions, value: any) => {
    setOptions((prev) => ({ ...prev, [id]: { ...prev[id], [key]: value } }));
  };

  const enabledComponents = components.filter((c) => options[c.id]?.enabled);

  const handlePreview = async () => {
    setGenerating(true);
    setMsg("");
    try {
      const res = await fetch(`${GATEWAY_API}/broker/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "generate-replication-scripts",
          manifest: manifestName,
          environment,
          level,
          components: enabledComponents.map((c) => ({ ...c, options: options[c.id] })),
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setPreview(typeof data.preview === "string" ? data.preview : JSON.stringify(data.scripts || data, null, 2));
      } else {
        setMsg("Failed to generate preview.");
      }
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
    setGenerating(false);
  };

  const handleGenerate = async (promote: boolean) => {
    setGenerating(true);
    setMsg("");
    try {
      const genRes = await fetch(`${GATEWAY_API}/broker/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "generate-replication-scripts",
          manifest: manifestName,
          environment,
          level,
          components: enabledComponents.map((c) => ({ ...c, options: options[c.id] })),
        }),
      });
      if (!genRes.ok) { setMsg("Script generation failed."); setGenerating(false); return; }
      const genData = await genRes.json();
      const scripts: any[] = genData.scripts || [genData];

      const registered: RegisteredScript[] = [];
      for (const script of scripts) {
        const regRes = await fetch(`${GATEWAY_API}/deployments/scripts`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: script.name, description: script.description || "", content: script.content, level }),
        });
        if (regRes.ok) {
          registered.push({ name: script.name, description: script.description || "", content: script.content, level });
          // Link script to component if provided
          if (componentId) {
            await fetch(`${GATEWAY_API}/deployments/components/${componentId}/scripts`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ script_name: script.name }),
            }).catch(() => {});
          }
        }
      }

      if (promote && registered.length > 0) {
        await fetch(`${GATEWAY_API}/deployments/environments/${environment}/promote`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ scripts: registered.map((s) => s.name) }),
        });
      }

      setMsg(`Registered ${registered.length} script(s).`);
      onComplete(registered);
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    }
    setGenerating(false);
  };

  if (loading) return <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>Loading manifest...</div>;

  return (
    <div style={styles.container}>
      <div style={styles.headerRow}>
        <h2 style={styles.title}>Script from Discovery — {manifestName}{componentName ? ` · ${componentName}` : ""}</h2>
        <button style={styles.secondaryButton} onClick={onCancel}>Cancel</button>
      </div>

      {/* Component Selection */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Discovered Components</span>
        {components.length === 0 && <span style={{ fontSize: "0.85rem", color: "#8888a0" }}>No components found in manifest.</span>}
        {components.map((c) => (
          <div key={c.id} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "8px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" }}>
            <label style={styles.checkLabel}>
              <input type="checkbox" checked={options[c.id]?.enabled ?? false} onChange={(e) => setOpt(c.id, "enabled", e.target.checked)} style={{ accentColor: "#6cffa0" }} />
              <span style={{ fontWeight: 600, color: "#e0e0e8", fontSize: "0.85rem" }}>{c.name}</span>
              <span style={{ fontSize: "0.7rem", color: "#8888a0", marginLeft: 8 }}>{c.type}{c.host ? ` · ${c.host}` : ""}</span>
            </label>
            {options[c.id]?.enabled && (
              <div style={{ display: "flex", gap: 12, flexWrap: "wrap", paddingLeft: 24 }}>
                <label style={styles.fieldLabel}>
                  Restart
                  <select style={styles.select} value={options[c.id].restartMethod} onChange={(e) => setOpt(c.id, "restartMethod", e.target.value)}>
                    {RESTART_METHODS.map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                </label>
                <label style={styles.fieldLabel}>
                  Health URL
                  <input style={{ ...styles.input, width: 220 }} value={options[c.id].healthCheckUrl} onChange={(e) => setOpt(c.id, "healthCheckUrl", e.target.value)} />
                </label>
                <label style={styles.checkLabel}>
                  <input type="checkbox" checked={options[c.id].backup} onChange={(e) => setOpt(c.id, "backup", e.target.checked)} style={{ accentColor: "#6cffa0" }} />
                  Backup before deploy
                </label>
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Level Selector */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Script Level</span>
        <div style={styles.tabRow}>
          {LEVELS.map((l) => (
            <button key={l.level} style={level === l.level ? styles.activeTab : styles.tab} onClick={() => setLevel(l.level)}>
              {l.label}
            </button>
          ))}
        </div>
      </div>

      {/* Preview */}
      {preview !== null && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Preview</span>
          <pre style={styles.codeBlock}>{preview}</pre>
        </div>
      )}

      {/* Actions */}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button style={styles.secondaryButton} onClick={handlePreview} disabled={generating || enabledComponents.length === 0}>
          {generating ? "Generating..." : "Preview Scripts"}
        </button>
        <button style={styles.acceptButton} onClick={() => handleGenerate(false)} disabled={generating || enabledComponents.length === 0}>
          Generate &amp; Register Scripts
        </button>
        <button style={styles.promoteButton} onClick={() => handleGenerate(true)} disabled={generating || enabledComponents.length === 0}>
          Register &amp; Promote to Dev
        </button>
      </div>

      {msg && <div style={{ fontSize: "0.85rem", color: msg.startsWith("Error") || msg.startsWith("Failed") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  card: { backgroundColor: "#12121a", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 10 },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  tabRow: { display: "flex", gap: 4, flexWrap: "wrap" },
  tab: { backgroundColor: "#12121a", color: "#8888a0", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", borderRadius: 6, padding: "6px 12px", fontSize: "0.8rem", cursor: "pointer" },
  activeTab: { backgroundColor: "#2a2a4a", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#6c8aff", borderRadius: 6, padding: "6px 12px", fontSize: "0.8rem", cursor: "pointer", fontWeight: 600 },
  codeBlock: { backgroundColor: "#0a0a12", color: "#e0e0e8", padding: 12, borderRadius: 8, fontSize: "0.75rem", overflow: "auto", maxHeight: 400, margin: 0, borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", fontFamily: "monospace" },
  checkLabel: { display: "flex", alignItems: "center", gap: 4, fontSize: "0.8rem", color: "#e0e0e8", cursor: "pointer" },
  fieldLabel: { display: "flex", flexDirection: "column", gap: 2, fontSize: "0.75rem", color: "#8888a0" },
  input: { backgroundColor: "#16162a", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a5a", borderRadius: 6, padding: "6px 10px", fontSize: "0.8rem" },
  select: { backgroundColor: "#16162a", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a5a", borderRadius: 6, padding: "6px 10px", fontSize: "0.8rem" },
  secondaryButton: { backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e", borderRadius: 8, padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" },
  acceptButton: { backgroundColor: "#2a4a2a", color: "#6cffa0", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a5a3a", borderRadius: 6, padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer", fontWeight: 600 },
  promoteButton: { backgroundColor: "#2a2a6a", color: "#6c8aff", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a8a", borderRadius: 6, padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer", fontWeight: 600 },
};
