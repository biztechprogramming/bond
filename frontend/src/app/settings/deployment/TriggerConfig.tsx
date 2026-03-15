"use client";

import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface TriggerSettings {
  webhookEnabled: boolean;
  branch: string;
  tagPattern: string;
  cronSchedule: string;
  manualOnly: boolean;
}

interface DeploymentTrigger {
  id: string;
  script_id: string;
  repo_url: string;
  branch: string;
  tag_pattern?: string;
  environment: string;
  cron_schedule?: string;
  enabled: boolean;
  created_at: number;
  updated_at: number;
}

interface Props {
  settings: TriggerSettings;
  onChange: (settings: TriggerSettings) => void;
  scriptId?: string;
  environment?: string;
}

export default function TriggerConfig({ settings, onChange, scriptId, environment }: Props) {
  const update = (patch: Partial<TriggerSettings>) => onChange({ ...settings, ...patch });
  const [triggers, setTriggers] = useState<DeploymentTrigger[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setLoading(true);
    fetch(`${GATEWAY_API}/deployments/triggers`)
      .then((r) => r.json())
      .then((data: DeploymentTrigger[]) => {
        let filtered = data;
        if (scriptId) filtered = filtered.filter((t) => t.script_id === scriptId);
        if (environment) filtered = filtered.filter((t) => t.environment === environment);
        setTriggers(filtered);
      })
      .catch(() => setTriggers([]))
      .finally(() => setLoading(false));
  }, [scriptId, environment]);

  const toggleTrigger = async (id: string, enabled: boolean) => {
    const action = enabled ? "enable" : "disable";
    try {
      await fetch(`${GATEWAY_API}/deployments/triggers/${id}/${action}`, { method: "PUT" });
      setTriggers((prev) => prev.map((t) => (t.id === id ? { ...t, enabled } : t)));
    } catch { /* ignore */ }
  };

  const removeTrigger = async (id: string) => {
    try {
      await fetch(`${GATEWAY_API}/deployments/triggers/${id}`, { method: "DELETE" });
      setTriggers((prev) => prev.filter((t) => t.id !== id));
    } catch { /* ignore */ }
  };

  return (
    <div style={styles.container}>
      <h3 style={styles.sectionTitle}>Triggers</h3>

      <label style={styles.checkRow}>
        <input
          type="checkbox"
          checked={settings.webhookEnabled}
          onChange={(e) => update({ webhookEnabled: e.target.checked, manualOnly: false })}
        />
        <span style={styles.checkLabel}>Push-to-deploy (webhook)</span>
      </label>

      {settings.webhookEnabled && (
        <div style={styles.subFields}>
          <div style={styles.field}>
            <label style={styles.label}>Branch filter</label>
            <input
              style={styles.input}
              value={settings.branch}
              onChange={(e) => update({ branch: e.target.value })}
              placeholder="main"
            />
          </div>
          <div style={styles.field}>
            <label style={styles.label}>Tag pattern</label>
            <input
              style={styles.input}
              value={settings.tagPattern}
              onChange={(e) => update({ tagPattern: e.target.value })}
              placeholder="v*"
            />
            <div style={styles.hint}>Leave empty to ignore tags</div>
          </div>
        </div>
      )}

      <div style={styles.field}>
        <label style={styles.label}>Cron schedule</label>
        <input
          style={styles.input}
          value={settings.cronSchedule}
          onChange={(e) => update({ cronSchedule: e.target.value })}
          placeholder="0 2 * * *"
        />
        <div style={styles.hint}>Format: minute hour day month weekday (e.g. &quot;0 2 * * *&quot; = daily at 2am)</div>
      </div>

      <label style={styles.checkRow}>
        <input
          type="checkbox"
          checked={settings.manualOnly}
          onChange={(e) => update({ manualOnly: e.target.checked, webhookEnabled: e.target.checked ? false : settings.webhookEnabled })}
        />
        <span style={styles.checkLabel}>Manual trigger only</span>
      </label>

      {/* Active triggers list */}
      {triggers.length > 0 && (
        <div style={styles.triggerList}>
          <h4 style={styles.triggerListTitle}>Active Triggers</h4>
          {triggers.map((t) => (
            <div key={t.id} style={styles.triggerRow}>
              <div style={styles.triggerInfo}>
                <span style={{ ...styles.triggerBadge, opacity: t.enabled ? 1 : 0.5 }}>
                  {t.enabled ? "ON" : "OFF"}
                </span>
                <span style={styles.triggerScript}>{t.script_id}</span>
                <span style={styles.triggerMeta}>{t.branch} &rarr; {t.environment}</span>
              </div>
              <div style={styles.triggerActions}>
                <button
                  style={styles.triggerBtn}
                  onClick={() => toggleTrigger(t.id, !t.enabled)}
                >
                  {t.enabled ? "Disable" : "Enable"}
                </button>
                <button
                  style={{ ...styles.triggerBtn, color: "#ff6b6b" }}
                  onClick={() => removeTrigger(t.id)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
      {loading && <div style={styles.hint}>Loading triggers...</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "16px",
    border: "1px solid #1e1e2e",
    display: "flex",
    flexDirection: "column",
    gap: "12px",
  },
  sectionTitle: { fontSize: "0.95rem", fontWeight: 600, color: "#e0e0e8", margin: 0 },
  checkRow: { display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" },
  checkLabel: { fontSize: "0.85rem", color: "#e0e0e8" },
  subFields: {
    marginLeft: "24px",
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  field: { display: "flex", flexDirection: "column", gap: "4px" },
  label: { fontSize: "0.8rem", color: "#8888a0" },
  input: {
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    outline: "none",
    maxWidth: "320px",
  },
  hint: { fontSize: "0.72rem", color: "#5a5a6e" },
  triggerList: {
    marginTop: "8px",
    borderTop: "1px solid #1e1e2e",
    paddingTop: "12px",
    display: "flex",
    flexDirection: "column",
    gap: "8px",
  },
  triggerListTitle: { fontSize: "0.85rem", fontWeight: 600, color: "#8888a0", margin: 0 },
  triggerRow: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    backgroundColor: "#0a0a12",
    borderRadius: "8px",
    padding: "8px 12px",
    gap: "8px",
  },
  triggerInfo: { display: "flex", alignItems: "center", gap: "8px", flex: 1, minWidth: 0 },
  triggerBadge: {
    fontSize: "0.65rem",
    fontWeight: 700,
    color: "#6c8aff",
    backgroundColor: "#1a1a2e",
    borderRadius: "4px",
    padding: "2px 6px",
  },
  triggerScript: { fontSize: "0.8rem", color: "#e0e0e8", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" },
  triggerMeta: { fontSize: "0.72rem", color: "#5a5a6e", whiteSpace: "nowrap" },
  triggerActions: { display: "flex", gap: "6px", flexShrink: 0 },
  triggerBtn: {
    background: "none",
    border: "1px solid #2a2a3e",
    borderRadius: "6px",
    color: "#8888a0",
    fontSize: "0.72rem",
    padding: "3px 8px",
    cursor: "pointer",
  },
};
