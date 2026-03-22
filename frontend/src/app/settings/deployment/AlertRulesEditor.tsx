import React, { useEffect, useState, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";

interface AlertRulesEditorProps {
  environment: string;
  onBack: () => void;
}

interface AlertRule {
  id: string;
  name: string;
  metric: string;
  operator: string;
  threshold: number;
  duration_seconds: number;
  severity: "info" | "warning" | "critical";
  enabled: boolean;
  trigger_count: number;
  actions: string[];
  applies_to: string;
  component_id?: string;
}

interface Component {
  id: string;
  name: string;
  display_name: string;
  component_type: string;
  icon: string | null;
}

const METRICS = [
  "CPU", "Memory", "Disk", "Error count", "Health check",
  "SSL expiry", "Process running", "Port reachable", "Custom command",
];
const OPERATORS = [">", ">=", "<", "<=", "==", "!="];
const SEVERITIES: AlertRule["severity"][] = ["info", "warning", "critical"];
const ACTIONS = ["emit alert", "auto-file issue", "notification", "custom script"];
const SEVERITY_COLORS = { info: "#6c8aff", warning: "#ffcc6c", critical: "#ff6c8a" };

const emptyRule = (): Omit<AlertRule, "id" | "trigger_count"> => ({
  name: "",
  metric: "CPU",
  operator: ">",
  threshold: 80,
  duration_seconds: 300,
  severity: "warning",
  enabled: true,
  actions: ["emit alert"],
  applies_to: "*",
});

export default function AlertRulesEditor({ environment, onBack }: AlertRulesEditorProps) {
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [editing, setEditing] = useState<Partial<AlertRule> | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [components, setComponents] = useState<Component[]>([]);

  const fetchRules = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/alert-rules/${environment}`);
      if (res.ok) setRules(await res.json());
    } catch { /* ignore */ }
  }, [environment]);

  useEffect(() => { fetchRules(); }, [fetchRules]);

  useEffect(() => {
    fetch(`${GATEWAY_API}/deployments/components?environment=${encodeURIComponent(environment)}`)
      .then(r => r.ok ? r.json() : [])
      .then(data => setComponents(Array.isArray(data) ? data : data.components || []))
      .catch(() => {});
  }, [environment]);

  const saveRule = async () => {
    if (!editing?.name) { setMsg("Name is required"); return; }
    setMsg("");
    try {
      const method = editingId ? "PUT" : "POST";
      const url = editingId
        ? `${GATEWAY_API}/deployments/alert-rules/${environment}/${editingId}`
        : `${GATEWAY_API}/deployments/alert-rules/${environment}`;
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(editing),
      });
      if (res.ok) {
        setEditing(null);
        setEditingId(null);
        await fetchRules();
      } else {
        setMsg("Failed to save rule");
      }
    } catch { setMsg("Failed to save rule"); }
  };

  const toggleRule = async (rule: AlertRule) => {
    try {
      await fetch(`${GATEWAY_API}/deployments/alert-rules/${environment}/${rule.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...rule, enabled: !rule.enabled }),
      });
      await fetchRules();
    } catch { /* ignore */ }
  };

  const deleteRule = async (id: string) => {
    try {
      await fetch(`${GATEWAY_API}/deployments/alert-rules/${environment}/${id}`, { method: "DELETE" });
      await fetchRules();
    } catch { /* ignore */ }
  };

  const toggleAction = (action: string) => {
    if (!editing) return;
    const current = editing.actions || [];
    setEditing({
      ...editing,
      actions: current.includes(action) ? current.filter((a) => a !== action) : [...current, action],
    });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
          Alert Rules — {environment}
        </h3>
        <div style={{ display: "flex", gap: "8px" }}>
          <button style={styles.primaryBtn} onClick={() => { setEditing(emptyRule()); setEditingId(null); }}>
            + Add Rule
          </button>
          <button style={styles.secondaryBtn} onClick={onBack}>Back</button>
        </div>
      </div>

      {/* Rule list */}
      <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
        {rules.length === 0 && !editing && (
          <div style={{ color: "#8888a0", fontSize: "0.85rem", padding: "16px", textAlign: "center" }}>
            No alert rules configured for this environment.
          </div>
        )}
        {rules.map((rule) => (
          <div key={rule.id} style={{
            display: "flex", justifyContent: "space-between", alignItems: "center",
            backgroundColor: "#1a1a2e", borderRadius: "8px", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
            padding: "10px 14px", opacity: rule.enabled ? 1 : 0.5,
          }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
              <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
                <span style={{ fontWeight: 600, color: "#e0e0e8", fontSize: "0.9rem" }}>{rule.name}</span>
                <span style={{
                  fontSize: "0.7rem", padding: "2px 6px", borderRadius: "4px",
                  backgroundColor: SEVERITY_COLORS[rule.severity] + "22",
                  color: SEVERITY_COLORS[rule.severity],
                }}>
                  {rule.severity}
                </span>
              </div>
              <span style={{ color: "#8888a0", fontSize: "0.8rem" }}>
                {rule.metric} {rule.operator} {rule.threshold} for {rule.duration_seconds}s
                {rule.component_id && (() => { const c = components.find(c => c.id === rule.component_id); return c ? ` · ${c.icon || ""}${c.display_name || c.name}` : ""; })()}
                {rule.trigger_count > 0 && ` — triggered ${rule.trigger_count}x`}
              </span>
            </div>
            <div style={{ display: "flex", gap: "6px" }}>
              <button style={styles.smallBtn} onClick={() => { setEditing({ ...rule }); setEditingId(rule.id); }}>Edit</button>
              <button style={styles.smallBtn} onClick={() => toggleRule(rule)}>
                {rule.enabled ? "Disable" : "Enable"}
              </button>
              <button style={{ ...styles.smallBtn, color: "#ff6c8a" }} onClick={() => deleteRule(rule.id)}>Delete</button>
            </div>
          </div>
        ))}
      </div>

      {/* Inline editor */}
      {editing && (
        <div style={{
          backgroundColor: "#1a1a2e", borderRadius: "8px", borderWidth: "1px", borderStyle: "solid", borderColor: "#6c8aff",
          padding: "16px", display: "flex", flexDirection: "column", gap: "10px",
        }}>
          <div style={{ fontWeight: 600, color: "#6c8aff", fontSize: "0.9rem" }}>
            {editingId ? "Edit Rule" : "New Rule"}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "10px" }}>
            <label style={styles.label}>
              Name
              <input
                style={styles.input}
                value={editing.name || ""}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
              />
            </label>
            <label style={styles.label}>
              Metric
              <select style={styles.input} value={editing.metric || "CPU"} onChange={(e) => setEditing({ ...editing, metric: e.target.value })}>
                {METRICS.map((m) => <option key={m} value={m}>{m}</option>)}
              </select>
            </label>
            <label style={styles.label}>
              Operator
              <select style={styles.input} value={editing.operator || ">"} onChange={(e) => setEditing({ ...editing, operator: e.target.value })}>
                {OPERATORS.map((o) => <option key={o} value={o}>{o}</option>)}
              </select>
            </label>
            <label style={styles.label}>
              Threshold
              <input
                type="number" style={styles.input}
                value={editing.threshold ?? 80}
                onChange={(e) => setEditing({ ...editing, threshold: Number(e.target.value) })}
              />
            </label>
            <label style={styles.label}>
              Duration (seconds)
              <input
                type="number" style={styles.input}
                value={editing.duration_seconds ?? 300}
                onChange={(e) => setEditing({ ...editing, duration_seconds: Number(e.target.value) })}
              />
            </label>
            <label style={styles.label}>
              Applies To
              <input
                style={styles.input}
                value={editing.applies_to || "*"}
                onChange={(e) => setEditing({ ...editing, applies_to: e.target.value })}
                placeholder="* for all servers"
              />
            </label>
            <label style={styles.label}>
              Component
              <select style={styles.input} value={editing.component_id || ""} onChange={(e) => setEditing({ ...editing, component_id: e.target.value || undefined })}>
                <option value="">None</option>
                {components.map(c => <option key={c.id} value={c.id}>{c.icon ? `${c.icon} ` : ""}{c.display_name || c.name}</option>)}
              </select>
            </label>
          </div>

          <div>
            <span style={{ color: "#8888a0", fontSize: "0.8rem" }}>Severity</span>
            <div style={{ display: "flex", gap: "8px", marginTop: "4px" }}>
              {SEVERITIES.map((s) => (
                <label key={s} style={{ display: "flex", alignItems: "center", gap: "4px", color: SEVERITY_COLORS[s], fontSize: "0.85rem", cursor: "pointer" }}>
                  <input type="radio" name="severity" checked={editing.severity === s} onChange={() => setEditing({ ...editing, severity: s })} />
                  {s}
                </label>
              ))}
            </div>
          </div>

          <div>
            <span style={{ color: "#8888a0", fontSize: "0.8rem" }}>Actions</span>
            <div style={{ display: "flex", gap: "8px", marginTop: "4px", flexWrap: "wrap" }}>
              {ACTIONS.map((a) => (
                <label key={a} style={{ display: "flex", alignItems: "center", gap: "4px", color: "#e0e0e8", fontSize: "0.85rem", cursor: "pointer" }}>
                  <input type="checkbox" checked={(editing.actions || []).includes(a)} onChange={() => toggleAction(a)} />
                  {a}
                </label>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", gap: "8px" }}>
            <button style={styles.primaryBtn} onClick={saveRule}>Save</button>
            <button style={styles.secondaryBtn} onClick={() => { setEditing(null); setEditingId(null); }}>Cancel</button>
          </div>
        </div>
      )}

      {msg && <div style={{ fontSize: "0.85rem", color: msg.includes("Failed") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  primaryBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  secondaryBtn: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  smallBtn: {
    backgroundColor: "transparent",
    color: "#8888a0",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "6px",
    padding: "4px 10px",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
  label: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "4px",
    color: "#8888a0",
    fontSize: "0.8rem",
  },
  input: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "6px",
    padding: "6px 10px",
    fontSize: "0.85rem",
  },
};
