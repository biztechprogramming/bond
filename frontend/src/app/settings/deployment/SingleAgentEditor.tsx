import React, { useState } from "react";
import { callReducer } from "@/hooks/useSpacetimeDB";

interface WorkspaceMount {
  id?: string;
  host_path: string;
  mount_name: string;
  container_path: string;
  readonly: boolean;
}

interface ChannelConfig {
  channel: string;
  enabled: boolean;
  sandbox_override: string | null;
}

interface Agent {
  id: string;
  name: string;
  display_name: string;
  system_prompt: string;
  model: string;
  utility_model: string;
  sandbox_image: string | null;
  workspace_mounts: WorkspaceMount[];
  channels: ChannelConfig[];
}

interface Props {
  agent: Agent;
  sharedModel: string;
  sharedUtilityModel: string;
  availableModels: { id: string; name: string }[];
  onBack: () => void;
  onSaved: () => void;
}

const ALL_CHANNELS = ["webchat", "signal", "telegram", "discord", "whatsapp", "email", "slack"];

const DEFAULT_MODELS = [
  "anthropic/claude-sonnet-4-20250514",
  "anthropic/claude-opus-4-6",
];

export default function SingleAgentEditor({ agent, sharedModel, sharedUtilityModel, availableModels, onBack, onSaved }: Props) {
  const [displayName, setDisplayName] = useState(agent.display_name);
  const [useSharedModel, setUseSharedModel] = useState(agent.model === sharedModel);
  const [modelOverride, setModelOverride] = useState(agent.model);
  const [useSharedUtility, setUseSharedUtility] = useState(agent.utility_model === sharedUtilityModel);
  const [utilityOverride, setUtilityOverride] = useState(agent.utility_model);
  const [systemPrompt, setSystemPrompt] = useState(agent.system_prompt);
  const [channels, setChannels] = useState<ChannelConfig[]>(agent.channels || []);
  const [mounts] = useState<WorkspaceMount[]>(agent.workspace_mounts || []);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState("");

  const modelOptions = availableModels.length > 0
    ? availableModels.filter((m, i, arr) => arr.findIndex((x) => x.id === m.id) === i)
    : DEFAULT_MODELS.map((id) => ({ id, name: id }));

  const toggleChannel = (ch: string) => {
    const exists = channels.find((c) => c.channel === ch);
    if (exists) {
      setChannels(channels.filter((c) => c.channel !== ch));
    } else {
      setChannels([...channels, { channel: ch, enabled: true, sandbox_override: null }]);
    }
  };

  const save = async () => {
    setSaving(true);
    setMsg("");
    try {
      const body = {
        name: agent.name,
        display_name: displayName,
        system_prompt: systemPrompt,
        model: useSharedModel ? sharedModel : modelOverride,
        utility_model: useSharedUtility ? sharedUtilityModel : utilityOverride,
        sandbox_image: agent.sandbox_image,
        workspace_mounts: mounts.map((m) => ({
          host_path: m.host_path,
          mount_name: m.mount_name,
          container_path: m.container_path,
          readonly: true, // Always readonly for deploy agents
        })),
        channels: channels.map((c) => ({
          channel: c.channel,
          enabled: c.enabled,
          sandbox_override: c.sandbox_override,
        })),
      };

      const ok = callReducer(conn => conn.reducers.updateAgent({
        id: agent.id,
        name: agent.name,
        displayName: displayName,
        systemPrompt: systemPrompt,
        model: useSharedModel ? sharedModel : modelOverride,
        utilityModel: useSharedUtility ? sharedUtilityModel : utilityOverride,
        tools: "",
        sandboxImage: agent.sandbox_image || "",
        maxIterations: 200,
        isActive: true,
        isDefault: false,
      }));

      if (ok) {
        setMsg("Saved.");
        onSaved();
      } else {
        setMsg("Error: No SpacetimeDB connection");
      }
    } catch {
      setMsg("Failed to save.");
    }
    setSaving(false);
  };

  const resetToShared = async () => {
    setUseSharedModel(true);
    setModelOverride(sharedModel);
    setUseSharedUtility(true);
    setUtilityOverride(sharedUtilityModel);
  };

  const envName = agent.name.replace("deploy-", "");

  return (
    <div style={styles.container}>
      <button style={styles.backLink} onClick={onBack}>&larr; Back to Dashboard</button>
      <h2 style={styles.title}>
        Editing: {displayName || agent.name} <span style={styles.slug}>({agent.name})</span>
      </h2>

      {msg && <div style={{ ...styles.msg, color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}

      <div style={styles.section}>
        <h3 style={styles.sectionTitle}>Identity</h3>
        <div style={styles.fieldRow}>
          <div style={styles.field}>
            <label style={styles.label}>Display Name</label>
            <input style={styles.input} value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
          </div>
          <div style={styles.field}>
            <label style={styles.label}>Slug (read-only)</label>
            <div style={styles.readOnly}>{agent.name}</div>
          </div>
        </div>
      </div>

      <div style={styles.section}>
        <h3 style={styles.sectionTitle}>Model</h3>
        <div style={styles.field}>
          <label style={styles.checkboxLabel}>
            <input
              type="checkbox"
              checked={useSharedModel}
              onChange={(e) => setUseSharedModel(e.target.checked)}
              style={styles.checkbox}
            />
            Use shared model ({sharedModel.split("/").pop()})
          </label>
          {!useSharedModel && (
            <select style={{ ...styles.select, marginTop: "8px" }} value={modelOverride} onChange={(e) => setModelOverride(e.target.value)}>
              {modelOptions.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              {modelOverride && !modelOptions.find((m) => m.id === modelOverride) && (
                <option value={modelOverride}>{modelOverride}</option>
              )}
            </select>
          )}
        </div>

        <div style={{ ...styles.field, marginTop: "12px" }}>
          <label style={styles.checkboxLabel}>
            <input
              type="checkbox"
              checked={useSharedUtility}
              onChange={(e) => setUseSharedUtility(e.target.checked)}
              style={styles.checkbox}
            />
            Use shared utility model ({sharedUtilityModel.split("/").pop()})
          </label>
          {!useSharedUtility && (
            <select style={{ ...styles.select, marginTop: "8px" }} value={utilityOverride} onChange={(e) => setUtilityOverride(e.target.value)}>
              {modelOptions.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
              {utilityOverride && !modelOptions.find((m) => m.id === utilityOverride) && (
                <option value={utilityOverride}>{utilityOverride}</option>
              )}
            </select>
          )}
        </div>
      </div>

      <div style={styles.section}>
        <h3 style={styles.sectionTitle}>System Prompt (appended to default)</h3>
        <p style={styles.hint}>
          The base deployment prompt for {envName} is always included. Add environment-specific instructions below.
        </p>
        <textarea
          style={{ ...styles.input, minHeight: "120px", resize: "vertical" }}
          value={systemPrompt}
          onChange={(e) => setSystemPrompt(e.target.value)}
          placeholder="Extra instructions for this environment..."
        />
      </div>

      <div style={styles.section}>
        <h3 style={styles.sectionTitle}>Channels</h3>
        <div style={styles.checkboxGrid}>
          {ALL_CHANNELS.map((ch) => (
            <label key={ch} style={styles.checkboxLabel}>
              <input
                type="checkbox"
                checked={channels.some((c) => c.channel === ch)}
                onChange={() => toggleChannel(ch)}
                style={styles.checkbox}
              />
              {ch}
            </label>
          ))}
        </div>
      </div>

      <div style={styles.section}>
        <h3 style={styles.sectionTitle}>Workspace Mounts (read-only enforced)</h3>
        {mounts.length === 0 ? (
          <p style={styles.hint}>No workspace mounts configured.</p>
        ) : (
          mounts.map((m, i) => (
            <div key={i} style={styles.mountRow}>
              <span style={styles.mountPath}>{m.host_path}</span>
              <span style={styles.mountArrow}>&rarr;</span>
              <span style={styles.mountPath}>{m.container_path}</span>
              <span style={styles.roTag}>RO</span>
            </div>
          ))
        )}
      </div>

      <div style={styles.buttonRow}>
        <button style={styles.button} onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save"}
        </button>
        <button style={styles.secondaryButton} onClick={onBack}>Cancel</button>
        <button style={styles.secondaryButton} onClick={resetToShared}>Reset to Shared Defaults</button>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: "16px" },
  backLink: {
    background: "none",
    border: "none",
    color: "#6c8aff",
    fontSize: "0.9rem",
    cursor: "pointer",
    padding: 0,
    textAlign: "left" as const,
    alignSelf: "flex-start",
  },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", margin: 0 },
  slug: { fontSize: "0.85rem", color: "#8888a0", fontWeight: 400 },
  msg: { fontSize: "0.85rem", padding: "8px 0" },
  section: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "20px",
    border: "1px solid #1e1e2e",
  },
  sectionTitle: { fontSize: "0.95rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 12px 0" },
  fieldRow: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" },
  field: {},
  label: { display: "block", fontSize: "0.85rem", color: "#8888a0", marginBottom: "6px", fontWeight: 500 },
  input: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
    boxSizing: "border-box" as const,
  },
  select: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
  },
  readOnly: { color: "#8888a0", fontSize: "0.95rem", padding: "10px 12px", backgroundColor: "#1e1e2e", borderRadius: "8px" },
  checkboxGrid: { display: "flex", flexWrap: "wrap" as const, gap: "8px 16px" },
  checkboxLabel: { display: "flex", alignItems: "center", gap: "6px", color: "#e0e0e8", fontSize: "0.85rem", cursor: "pointer" },
  checkbox: { accentColor: "#6c8aff" },
  hint: { fontSize: "0.8rem", color: "#5a5a6e", margin: "0 0 8px 0" },
  mountRow: { display: "flex", alignItems: "center", gap: "8px", marginBottom: "6px", fontSize: "0.85rem" },
  mountPath: { color: "#e0e0e8", backgroundColor: "#1e1e2e", padding: "6px 10px", borderRadius: "6px" },
  mountArrow: { color: "#5a5a6e" },
  roTag: { color: "#ffcc44", fontSize: "0.75rem", fontWeight: 600, backgroundColor: "#2a2a1a", padding: "2px 6px", borderRadius: "4px" },
  buttonRow: { display: "flex", gap: "12px" },
  button: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    cursor: "pointer",
  },
};
