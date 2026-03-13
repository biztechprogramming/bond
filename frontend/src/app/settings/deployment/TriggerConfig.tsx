import React from "react";

interface TriggerSettings {
  webhookEnabled: boolean;
  branch: string;
  tagPattern: string;
  cronSchedule: string;
  manualOnly: boolean;
}

interface Props {
  settings: TriggerSettings;
  onChange: (settings: TriggerSettings) => void;
}

export default function TriggerConfig({ settings, onChange }: Props) {
  const update = (patch: Partial<TriggerSettings>) => onChange({ ...settings, ...patch });

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
};
