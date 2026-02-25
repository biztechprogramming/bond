"use client";

import React, { useEffect, useState, useCallback } from "react";

const API_BASE = "http://localhost:18790/api/v1/agents";

interface WorkspaceMount {
  id?: string;
  host_path: string;
  mount_name: string;
  readonly: boolean;
}

interface ChannelConfig {
  id?: string;
  channel: string;
  enabled: boolean;
  sandbox_override: string | null;
}

interface ToolInfo {
  name: string;
  description: string;
}

interface Agent {
  id: string;
  name: string;
  display_name: string;
  system_prompt: string;
  model: string;
  sandbox_image: string | null;
  tools: string[];
  max_iterations: number;
  auto_rag: boolean;
  auto_rag_limit: number;
  is_default: boolean;
  is_active: boolean;
  workspace_mounts: WorkspaceMount[];
  channels: ChannelConfig[];
}

const MODELS = [
  "anthropic/claude-sonnet-4-20250514",
  "anthropic/claude-opus-4-6",
  "openai/gpt-4o",
  "google/gemini-2.0-flash",
];

const ALL_CHANNELS = ["webchat", "signal", "telegram", "discord", "whatsapp", "email", "slack"];

// Directory browser modal
function DirBrowser({
  onSelect,
  onClose,
}: {
  onSelect: (path: string) => void;
  onClose: () => void;
}) {
  const [currentPath, setCurrentPath] = useState("/home");
  const [dirs, setDirs] = useState<{ name: string; path: string }[]>([]);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [showHidden, setShowHidden] = useState(false);

  const showHiddenRef = React.useRef(showHidden);
  showHiddenRef.current = showHidden;

  const browse = useCallback(async (path: string, hidden?: boolean) => {
    const h = hidden ?? showHiddenRef.current;
    setLoading(true);
    try {
      const res = await fetch(
        `http://localhost:18790/api/v1/agents/browse-dirs?path=${encodeURIComponent(path)}&show_hidden=${h}`
      );
      if (res.ok) {
        const data = await res.json();
        setCurrentPath(data.current);
        setParentPath(data.parent);
        setDirs(data.directories);
      }
    } catch {
      // ignore
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    browse(currentPath, false);
  }, []);

  return (
    <div style={modalStyles.overlay} onClick={onClose}>
      <div style={modalStyles.modal} onClick={(e) => e.stopPropagation()}>
        <div style={modalStyles.header}>
          <span style={modalStyles.title}>Select Directory</span>
          <button style={modalStyles.close} onClick={onClose}>✕</button>
        </div>
        <div style={modalStyles.pathBar}>
          <span style={{ color: "#6c8aff", fontSize: "0.85rem", wordBreak: "break-all", flex: 1 }}>
            {currentPath}
          </span>
          <label style={{ display: "flex", alignItems: "center", gap: "4px", color: "#8888a0", fontSize: "0.8rem", flexShrink: 0, cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={showHidden}
              onChange={(e) => {
                setShowHidden(e.target.checked);
                browse(currentPath, e.target.checked);
              }}
              style={{ accentColor: "#6c8aff" }}
            />
            Hidden
          </label>
          <button
            style={{ ...modalStyles.selectBtn, flexShrink: 0 }}
            onClick={() => onSelect(currentPath)}
          >
            Select This
          </button>
        </div>
        <div style={modalStyles.dirList}>
          {parentPath && (
            <div style={modalStyles.dirItem} onClick={() => browse(parentPath)}>
              📁 ..
            </div>
          )}
          {loading && <div style={{ color: "#8888a0", padding: "12px" }}>Loading...</div>}
          {dirs.map((d) => (
            <div
              key={d.path}
              style={modalStyles.dirItem}
              onClick={() => browse(d.path)}
            >
              📁 {d.name}
            </div>
          ))}
          {!loading && dirs.length === 0 && (
            <div style={{ color: "#8888a0", padding: "12px", fontSize: "0.85rem" }}>
              No subdirectories
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

const modalStyles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    top: 0, left: 0, right: 0, bottom: 0,
    backgroundColor: "rgba(0,0,0,0.7)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "12px",
    width: "500px",
    height: "70vh",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  },
  header: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "16px 20px",
    borderBottom: "1px solid #1e1e2e",
  },
  title: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  close: {
    background: "none",
    border: "none",
    color: "#8888a0",
    fontSize: "1.2rem",
    cursor: "pointer",
  },
  pathBar: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    padding: "12px 20px",
    borderBottom: "1px solid #1e1e2e",
  },
  selectBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "6px",
    padding: "6px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  dirList: {
    overflowY: "scroll" as const,
    flex: 1,
    minHeight: 0,
    maxHeight: "400px",
    WebkitOverflowScrolling: "touch",
  },
  dirItem: {
    padding: "10px 20px",
    cursor: "pointer",
    fontSize: "0.9rem",
    color: "#e0e0e8",
    borderBottom: "1px solid #1a1a2a",
  },
};

export default function AgentsSettingsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [sandboxImages, setSandboxImages] = useState<string[]>([]);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [browsingMountIndex, setBrowsingMountIndex] = useState<number | null>(null);

  const fetchData = useCallback(async () => {
    try {
      const [agentsRes, toolsRes, imagesRes] = await Promise.all([
        fetch(API_BASE),
        fetch(`${API_BASE}/tools`),
        fetch(`${API_BASE}/sandbox-images`),
      ]);
      setAgents(await agentsRes.json());
      setTools(await toolsRes.json());
      setSandboxImages(await imagesRes.json());
    } catch {
      // API not available
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const newAgent = (): Agent => ({
    id: "",
    name: "",
    display_name: "",
    system_prompt: "",
    model: MODELS[0],
    sandbox_image: null,
    tools: [],
    max_iterations: 25,
    auto_rag: true,
    auto_rag_limit: 5,
    is_default: false,
    is_active: true,
    workspace_mounts: [],
    channels: [{ channel: "webchat", enabled: true, sandbox_override: null }],
  });

  const startCreate = () => {
    setEditing(newAgent());
    setIsNew(true);
    setMsg("");
  };

  const startEdit = (agent: Agent) => {
    setEditing({ ...agent });
    setIsNew(false);
    setMsg("");
  };

  const save = async () => {
    if (!editing) return;
    setMsg("");
    try {
      const body = {
        name: editing.name,
        display_name: editing.display_name,
        system_prompt: editing.system_prompt,
        model: editing.model,
        sandbox_image: editing.sandbox_image,
        tools: editing.tools,
        max_iterations: editing.max_iterations,
        auto_rag: editing.auto_rag,
        auto_rag_limit: editing.auto_rag_limit,
        workspace_mounts: editing.workspace_mounts.map((m) => ({
          host_path: m.host_path,
          mount_name: m.mount_name,
          readonly: m.readonly,
        })),
        channels: editing.channels.map((c) => ({
          channel: c.channel,
          enabled: c.enabled,
          sandbox_override: c.sandbox_override,
        })),
      };

      const url = isNew ? API_BASE : `${API_BASE}/${editing.id}`;
      const method = isNew ? "POST" : "PUT";
      const res = await fetch(url, {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (res.ok) {
        setMsg("Saved successfully.");
        setEditing(null);
        await fetchData();
      } else {
        const data = await res.json();
        setMsg(`Error: ${data.detail || "Save failed"}`);
      }
    } catch {
      setMsg("Failed to save.");
    }
  };

  const deleteAgent = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/${id}`, { method: "DELETE" });
      if (res.ok) {
        setMsg("Deleted.");
        setEditing(null);
        await fetchData();
      } else {
        const data = await res.json();
        setMsg(`Error: ${data.detail || "Delete failed"}`);
      }
    } catch {
      setMsg("Failed to delete.");
    }
  };

  const setDefault = async (id: string) => {
    try {
      const res = await fetch(`${API_BASE}/${id}/default`, { method: "POST" });
      if (res.ok) {
        setMsg("Default updated.");
        await fetchData();
      }
    } catch {
      setMsg("Failed to set default.");
    }
  };

  const toggleTool = (toolName: string) => {
    if (!editing) return;
    const newTools = editing.tools.includes(toolName)
      ? editing.tools.filter((t) => t !== toolName)
      : [...editing.tools, toolName];
    setEditing({ ...editing, tools: newTools });
  };

  const toggleChannel = (channel: string) => {
    if (!editing) return;
    const existing = editing.channels.find((c) => c.channel === channel);
    if (existing) {
      setEditing({
        ...editing,
        channels: editing.channels.filter((c) => c.channel !== channel),
      });
    } else {
      setEditing({
        ...editing,
        channels: [...editing.channels, { channel, enabled: true, sandbox_override: null }],
      });
    }
  };

  const addMount = () => {
    if (!editing) return;
    setEditing({
      ...editing,
      workspace_mounts: [
        ...editing.workspace_mounts,
        { host_path: "", mount_name: "", readonly: false },
      ],
    });
  };

  const removeMount = (index: number) => {
    if (!editing) return;
    setEditing({
      ...editing,
      workspace_mounts: editing.workspace_mounts.filter((_, i) => i !== index),
    });
  };

  const updateMount = (index: number, field: keyof WorkspaceMount, value: string | boolean) => {
    if (!editing) return;
    const mounts = [...editing.workspace_mounts];
    mounts[index] = { ...mounts[index], [field]: value };
    setEditing({ ...editing, workspace_mounts: mounts });
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <a href="/settings" style={styles.backLink}>
            &larr; Settings
          </a>
          <h1 style={styles.title}>Agent Management</h1>
        </div>
        <button style={styles.button} onClick={startCreate}>
          + New Agent
        </button>
      </header>

      {msg && <div style={styles.msg}>{msg}</div>}

      <div style={styles.content}>
        {!editing ? (
          <div style={styles.cardGrid}>
            {agents.map((agent) => (
              <div key={agent.id} style={styles.card} onClick={() => startEdit(agent)}>
                <div style={styles.cardHeader}>
                  <span style={styles.cardName}>{agent.display_name}</span>
                  {agent.is_default && <span style={styles.badge}>Default</span>}
                </div>
                <div style={styles.cardMeta}>{agent.model}</div>
                <div style={styles.cardMeta}>{agent.tools.length} tools enabled</div>
                <div style={styles.cardMeta}>
                  Channels: {agent.channels.map((c) => c.channel).join(", ") || "none"}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={styles.form}>
            <div style={styles.formRow}>
              <div style={styles.field}>
                <label style={styles.label}>Name (slug)</label>
                <input
                  style={styles.input}
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  placeholder="my-agent"
                />
              </div>
              <div style={styles.field}>
                <label style={styles.label}>Display Name</label>
                <input
                  style={styles.input}
                  value={editing.display_name}
                  onChange={(e) => setEditing({ ...editing, display_name: e.target.value })}
                  placeholder="My Agent"
                />
              </div>
            </div>

            <div style={styles.field}>
              <label style={styles.label}>System Prompt</label>
              <textarea
                style={{ ...styles.input, minHeight: "120px", resize: "vertical" }}
                value={editing.system_prompt}
                onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })}
              />
            </div>

            <div style={styles.formRow}>
              <div style={styles.field}>
                <label style={styles.label}>Model</label>
                <select
                  style={styles.select}
                  value={editing.model}
                  onChange={(e) => setEditing({ ...editing, model: e.target.value })}
                >
                  {MODELS.map((m) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              </div>
              <div style={styles.field}>
                <label style={styles.label}>Sandbox Image</label>
                <select
                  style={styles.select}
                  value={editing.sandbox_image || ""}
                  onChange={(e) =>
                    setEditing({ ...editing, sandbox_image: e.target.value || null })
                  }
                >
                  <option value="">None (host execution)</option>
                  {sandboxImages.map((img) => (
                    <option key={img} value={img}>
                      {img}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div style={styles.field}>
              <label style={styles.label}>Tools</label>
              <div style={styles.checkboxGrid}>
                {tools.map((tool) => (
                  <label key={tool.name} style={styles.checkboxLabel} title={tool.description}>
                    <input
                      type="checkbox"
                      checked={editing.tools.includes(tool.name)}
                      onChange={() => toggleTool(tool.name)}
                      style={styles.checkbox}
                    />
                    {tool.name}
                  </label>
                ))}
              </div>
            </div>

            <div style={styles.field}>
              <label style={styles.label}>Channels</label>
              <div style={styles.checkboxGrid}>
                {ALL_CHANNELS.map((ch) => (
                  <label key={ch} style={styles.checkboxLabel}>
                    <input
                      type="checkbox"
                      checked={editing.channels.some((c) => c.channel === ch)}
                      onChange={() => toggleChannel(ch)}
                      style={styles.checkbox}
                    />
                    {ch}
                  </label>
                ))}
              </div>
            </div>

            <div style={styles.field}>
              <label style={styles.label}>
                Workspace Mounts{" "}
                <button style={styles.smallButton} onClick={addMount}>
                  + Add
                </button>
              </label>
              {editing.workspace_mounts.map((mount, i) => (
                <div key={i} style={styles.mountRow}>
                  <input
                    style={{ ...styles.input, flex: 1 }}
                    value={mount.host_path}
                    onChange={(e) => updateMount(i, "host_path", e.target.value)}
                    placeholder="/path/on/host"
                  />
                  <button
                    style={styles.smallButton}
                    onClick={() => setBrowsingMountIndex(i)}
                    title="Browse directories"
                  >
                    📂
                  </button>
                  <input
                    style={{ ...styles.input, width: "140px" }}
                    value={mount.mount_name}
                    onChange={(e) => updateMount(i, "mount_name", e.target.value)}
                    placeholder="name"
                  />
                  <label style={styles.checkboxLabel}>
                    <input
                      type="checkbox"
                      checked={mount.readonly}
                      onChange={(e) => updateMount(i, "readonly", e.target.checked)}
                      style={styles.checkbox}
                    />
                    RO
                  </label>
                  <button style={styles.dangerSmall} onClick={() => removeMount(i)}>
                    X
                  </button>
                </div>
              ))}
            </div>

            <div style={styles.formRow}>
              <div style={styles.field}>
                <label style={styles.label}>Max Iterations</label>
                <input
                  type="number"
                  style={{ ...styles.input, width: "100px" }}
                  value={editing.max_iterations}
                  onChange={(e) =>
                    setEditing({ ...editing, max_iterations: parseInt(e.target.value) || 25 })
                  }
                />
              </div>
              <div style={styles.field}>
                <label style={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={editing.auto_rag}
                    onChange={(e) => setEditing({ ...editing, auto_rag: e.target.checked })}
                    style={styles.checkbox}
                  />
                  Auto-RAG
                </label>
                {editing.auto_rag && (
                  <input
                    type="number"
                    style={{ ...styles.input, width: "80px", marginLeft: "8px" }}
                    value={editing.auto_rag_limit}
                    onChange={(e) =>
                      setEditing({ ...editing, auto_rag_limit: parseInt(e.target.value) || 5 })
                    }
                    placeholder="limit"
                  />
                )}
              </div>
            </div>

            {browsingMountIndex !== null && (
              <DirBrowser
                onSelect={(path) => {
                  updateMount(browsingMountIndex, "host_path", path);
                  // Auto-fill mount_name from last path segment
                  const name = path.split("/").filter(Boolean).pop() || "";
                  if (!editing.workspace_mounts[browsingMountIndex].mount_name) {
                    updateMount(browsingMountIndex, "mount_name", name);
                  }
                  setBrowsingMountIndex(null);
                }}
                onClose={() => setBrowsingMountIndex(null)}
              />
            )}

            <div style={styles.buttonRow}>
              <button style={styles.button} onClick={save}>
                Save
              </button>
              {!isNew && !editing.is_default && (
                <>
                  <button style={styles.secondaryButton} onClick={() => setDefault(editing.id)}>
                    Set Default
                  </button>
                  <button style={styles.dangerButton} onClick={() => deleteAgent(editing.id)}>
                    Delete
                  </button>
                </>
              )}
              <button
                style={styles.secondaryButton}
                onClick={() => {
                  setEditing(null);
                  setMsg("");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    maxWidth: "900px",
    margin: "0 auto",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 24px",
    borderBottom: "1px solid #1e1e2e",
  },
  backLink: { color: "#6c8aff", textDecoration: "none", fontSize: "0.9rem" },
  title: { fontSize: "1.5rem", fontWeight: 700, margin: 0 },
  content: { flex: 1, overflowY: "auto", padding: "24px" },
  msg: {
    padding: "8px 24px",
    fontSize: "0.85rem",
    color: "#6cffa0",
    backgroundColor: "#12121a",
  },
  cardGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))",
    gap: "16px",
  },
  card: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "20px",
    border: "1px solid #1e1e2e",
    cursor: "pointer",
    transition: "border-color 0.2s",
  },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  cardName: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8" },
  badge: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    padding: "2px 8px",
    borderRadius: "4px",
    fontSize: "0.7rem",
    fontWeight: 600,
  },
  cardMeta: { fontSize: "0.8rem", color: "#8888a0", marginTop: "4px" },
  form: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "24px",
    border: "1px solid #1e1e2e",
    display: "flex",
    flexDirection: "column",
    gap: "16px",
  },
  formRow: { display: "flex", gap: "16px" },
  field: { flex: 1 },
  label: {
    display: "block",
    fontSize: "0.85rem",
    color: "#8888a0",
    marginBottom: "6px",
    fontWeight: 500,
  },
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
  checkboxGrid: {
    display: "flex",
    flexWrap: "wrap" as const,
    gap: "8px 16px",
  },
  checkboxLabel: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  checkbox: { accentColor: "#6c8aff" },
  mountRow: {
    display: "flex",
    gap: "8px",
    alignItems: "center",
    marginBottom: "8px",
  },
  buttonRow: { display: "flex", gap: "12px", marginTop: "8px" },
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
  dangerButton: {
    backgroundColor: "#3a1a1a",
    color: "#ff6c8a",
    border: "1px solid #5a2a2a",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    cursor: "pointer",
  },
  smallButton: {
    backgroundColor: "#2a2a3e",
    color: "#6c8aff",
    border: "none",
    borderRadius: "4px",
    padding: "2px 8px",
    fontSize: "0.75rem",
    cursor: "pointer",
    marginLeft: "8px",
  },
  dangerSmall: {
    backgroundColor: "#3a1a1a",
    color: "#ff6c8a",
    border: "none",
    borderRadius: "4px",
    padding: "4px 8px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
};
