import React from "react";

interface SharedSettings {
  model: string;
  utility_model: string;
  sandbox_image: string;
}

interface Props {
  settings: SharedSettings;
  onChange: (settings: SharedSettings) => void;
  availableModels: { id: string; name: string }[];
  sandboxImages: string[];
  overrideWarning?: string;
  onSave: () => void;
  onResetAll?: () => void;
  saving?: boolean;
}

const DEFAULT_MODELS = [
  "anthropic/claude-sonnet-4-20250514",
  "anthropic/claude-opus-4-6",
];

export default function SharedSettingsForm({
  settings,
  onChange,
  availableModels,
  sandboxImages,
  overrideWarning,
  onSave,
  onResetAll,
  saving,
}: Props) {
  const modelOptions = availableModels.length > 0
    ? availableModels.filter((m, i, arr) => arr.findIndex((x) => x.id === m.id) === i)
    : DEFAULT_MODELS.map((id) => ({ id, name: id }));

  return (
    <div style={styles.container}>
      <div style={styles.grid}>
        <div style={styles.field}>
          <label style={styles.label}>Model</label>
          <select
            style={styles.select}
            value={settings.model}
            onChange={(e) => onChange({ ...settings, model: e.target.value })}
          >
            {modelOptions.map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
            {settings.model && !modelOptions.find((m) => m.id === settings.model) && (
              <option value={settings.model}>{settings.model}</option>
            )}
          </select>
        </div>

        <div style={styles.field}>
          <label style={styles.label}>Utility Model</label>
          <select
            style={styles.select}
            value={settings.utility_model}
            onChange={(e) => onChange({ ...settings, utility_model: e.target.value })}
          >
            {modelOptions.map((m) => (
              <option key={m.id} value={m.id}>{m.name}</option>
            ))}
            {settings.utility_model && !modelOptions.find((m) => m.id === settings.utility_model) && (
              <option value={settings.utility_model}>{settings.utility_model}</option>
            )}
          </select>
        </div>

        <div style={styles.field}>
          <label style={styles.label}>Sandbox Image</label>
          <select
            style={styles.select}
            value={settings.sandbox_image}
            onChange={(e) => onChange({ ...settings, sandbox_image: e.target.value })}
          >
            <option value="">None (host execution)</option>
            {sandboxImages.map((img) => (
              <option key={img} value={img}>{img}</option>
            ))}
            {settings.sandbox_image && !sandboxImages.includes(settings.sandbox_image) && (
              <option value={settings.sandbox_image}>{settings.sandbox_image}</option>
            )}
          </select>
        </div>
      </div>

      {overrideWarning && (
        <div style={styles.warning}>{overrideWarning}</div>
      )}

      <div style={styles.buttonRow}>
        <button style={styles.button} onClick={onSave} disabled={saving}>
          {saving ? "Saving..." : "Save Shared Settings"}
        </button>
        {onResetAll && (
          <button style={styles.dangerButton} onClick={onResetAll} disabled={saving}>
            Reset All Overrides &amp; Save
          </button>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "20px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    marginBottom: "16px",
  },
  grid: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr 1fr",
    gap: "16px",
  },
  field: {},
  label: {
    display: "block",
    fontSize: "0.85rem",
    color: "#8888a0",
    marginBottom: "6px",
    fontWeight: 500,
  },
  select: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
  },
  warning: {
    backgroundColor: "#2a2a1a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#aa8800",
    borderRadius: "8px",
    padding: "10px 14px",
    color: "#ffcc44",
    fontSize: "0.82rem",
    marginTop: "12px",
  },
  buttonRow: { display: "flex", gap: "12px", marginTop: "16px" },
  button: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  dangerButton: {
    backgroundColor: "#3a1a1a",
    color: "#ff6c8a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#5a2a2a",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    cursor: "pointer",
  },
};
