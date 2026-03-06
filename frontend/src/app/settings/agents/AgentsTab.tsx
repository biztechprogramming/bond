import React, { useEffect, useState, useCallback } from "react";
import DirBrowser from "@/components/shared/DirBrowser";

const API_BASE = "http://localhost:18790/api/v1/agents";

interface WorkspaceMount {
  id?: string;
  host_path: string;
  mount_name: string;
  container_path: string;
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
  utility_model: string;
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

// Fallbacks if the API is unreachable
const DEFAULT_MODELS = [
  "anthropic/claude-sonnet-4-20250514",
  "anthropic/claude-opus-4-6",
];

const ALL_CHANNELS = ["webchat", "signal", "telegram", "discord", "whatsapp", "email", "slack"];

export default function AgentsTab() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const allToolNames = tools.map((t) => t.name);
  const [sandboxImages, setSandboxImages] = useState<string[]>([]);
  const [editing, setEditing] = useState<Agent | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [browsingMountIndex, setBrowsingMountIndex] = useState<number | null>(null);
  const [allFragments, setAllFragments] = useState<{ id: string; name: string; display_name: string; category: string; is_active: number }[]>([]);
  const [agentFragments, setAgentFragments] = useState<{ id: string; display_name: string; category: string; enabled: number; rank: number; fragment_id?: string }[]>([]);
  const [pendingFragmentIds, setPendingFragmentIds] = useState<Set<string>>(new Set());
  const [availableModelsRaw, setAvailableModelsRaw] = useState<{ id: string; name: string }[]>([]);
  const availableModels = availableModelsRaw.filter((m, i, arr) => arr.findIndex((x) => x.id === m.id) === i);

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
      try {
        const fragRes = await fetch("http://localhost:18790/api/v1/prompts/fragments");
        if (fragRes.ok) setAllFragments(await fragRes.json());
      } catch { /* prompts API not available */ }
      try {
        const modelsRes = await fetch("http://localhost:18790/api/v1/settings/llm/models");
        if (modelsRes.ok) setAvailableModelsRaw(await modelsRes.json());
      } catch { /* models API not available */ }
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
    model: availableModels.length > 0 ? availableModels[0].id : DEFAULT_MODELS[0],
    utility_model: availableModels.length > 0 ? availableModels[0].id : DEFAULT_MODELS[0],
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
    setEditing({ ...newAgent(), tools: allToolNames });
    setIsNew(true);
    setAgentFragments([]);
    setPendingFragmentIds(new Set(allFragments.filter(f => f.is_active).map(f => f.id)));
    setMsg("");
  };

  const loadAgentFragments = async (agentId: string) => {
    try {
      const res = await fetch(`http://localhost:18790/api/v1/prompts/agents/${agentId}/fragments`);
      if (res.ok) setAgentFragments(await res.json());
      else setAgentFragments([]);
    } catch { setAgentFragments([]); }
  };

  const toggleAgentFragment = async (agentId: string, fragmentId: string, isAttached: boolean) => {
    try {
      if (isAttached) {
        await fetch(`http://localhost:18790/api/v1/prompts/agents/${agentId}/fragments/${fragmentId}`, { method: "DELETE" });
      } else {
        await fetch(`http://localhost:18790/api/v1/prompts/agents/${agentId}/fragments`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ fragment_id: fragmentId, rank: agentFragments.length }),
        });
      }
      await loadAgentFragments(agentId);
    } catch { /* ignore */ }
  };

  const startEdit = (agent: Agent) => {
    setEditing({
      ...agent,
      auto_rag: agent.auto_rag ?? true,
      auto_rag_limit: agent.auto_rag_limit ?? 5,
    });
    loadAgentFragments(agent.id);
    setPendingFragmentIds(new Set());
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
        utility_model: editing.utility_model,
        sandbox_image: editing.sandbox_image,
        tools: editing.tools,
        max_iterations: editing.max_iterations,
        auto_rag: editing.auto_rag,
        auto_rag_limit: editing.auto_rag_limit,
        workspace_mounts: editing.workspace_mounts?.map((m) => ({
          host_path: m.host_path,
          mount_name: m.mount_name,
          container_path: m.container_path || `/workspace/${m.mount_name}`,
          readonly: m.readonly,
        })) || [],
        channels: editing.channels?.map((c) => ({
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
        const saved = await res.json();

        // Attach pending fragments after creation
        if (isNew && saved?.id && pendingFragmentIds.size > 0) {
          const fragIds = Array.from(pendingFragmentIds);
          await Promise.all(fragIds.map((fid, i) =>
            fetch(`http://localhost:18790/api/v1/prompts/agents/${saved.id}/fragments`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ fragment_id: fid, rank: i }),
            })
          ));
          setPendingFragmentIds(new Set());
        }

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
        { host_path: "", mount_name: "", container_path: "", readonly: false },
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
    const updated = { ...mounts[index], [field]: value };
    // Auto-update container_path when mount_name changes (if container_path wasn't manually set)
    if (field === "mount_name" && typeof value === "string") {
      const oldDefault = `/workspace/${mounts[index].mount_name}`;
      if (!mounts[index].container_path || mounts[index].container_path === oldDefault) {
        updated.container_path = `/workspace/${value}`;
      }
    }
    mounts[index] = updated;
    setEditing({ ...editing, workspace_mounts: mounts });
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      {/* Top bar: contextual — list vs edit mode */}
      <div style={styles.topBar}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
          {editing ? (isNew ? "New Agent" : `Editing: ${editing.display_name || editing.name}`) : "Agent Management"}
        </h2>
        <div style={{ display: "flex", gap: "8px" }}>
          {editing ? (
            <>
              <button style={styles.button} onClick={save}>Save</button>
              <button style={styles.secondaryButton} onClick={() => { setEditing(null); setMsg(""); }}>Cancel</button>
            </>
          ) : (
            <button style={styles.button} onClick={startCreate}>+ New Agent</button>
          )}
        </div>
      </div>

      {msg && <div style={styles.msg}>{msg}</div>}

      <div style={{ flex: 1, overflowY: "auto" }}>
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
                  Channels: {agent.channels?.map((c) => c.channel).join(", ") || "none"}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div style={styles.form}>
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

            <div style={styles.field}>
              <label style={styles.label}>Model</label>
              <select
                style={styles.select}
                value={editing.model}
                onChange={(e) => setEditing({ ...editing, model: e.target.value })}
              >
                {(availableModels.length > 0 ? availableModels : DEFAULT_MODELS.map(id => ({ id, name: id }))).map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
                {editing.model && !availableModels.find(m => m.id === editing.model) && (
                  <option key={editing.model} value={editing.model}>{editing.model}</option>
                )}
              </select>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Utility Model</label>
              <select
                style={styles.select}
                value={editing.utility_model}
                onChange={(e) => setEditing({ ...editing, utility_model: e.target.value })}
              >
                {(availableModels.length > 0 ? availableModels : DEFAULT_MODELS.map(id => ({ id, name: id }))).map((m) => (
                  <option key={m.id} value={m.id}>{m.name}</option>
                ))}
                {editing.utility_model && !availableModels.find(m => m.id === editing.utility_model) && (
                  <option key={editing.utility_model} value={editing.utility_model}>{editing.utility_model}</option>
                )}
              </select>
              <div style={{ fontSize: "0.75rem", color: "#5a5a6e", marginTop: "2px" }}>
                Selects which prompt fragments to include each turn
              </div>
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Sandbox Image</label>
              <select
                style={styles.select}
                value={editing.sandbox_image || ""}
                onChange={(e) => setEditing({ ...editing, sandbox_image: e.target.value || null })}
              >
                <option value="">None (host execution)</option>
                {sandboxImages.map((img) => (
                  <option key={img} value={img}>{img}</option>
                ))}
              </select>
            </div>

            <div style={styles.field}>
              <label style={styles.label}>Max Iterations</label>
              <input
                type="number"
                style={styles.input}
                value={editing.max_iterations}
                onChange={(e) => setEditing({ ...editing, max_iterations: parseInt(e.target.value) || 25 })}
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Auto-RAG</label>
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                <label style={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={editing.auto_rag}
                    onChange={(e) => setEditing({ ...editing, auto_rag: e.target.checked })}
                    style={styles.checkbox}
                  />
                  Enabled
                </label>
                {editing.auto_rag && (
                  <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                    <span style={{ color: "#8888a0", fontSize: "0.85rem" }}>Limit:</span>
                    <input
                      type="number"
                      style={{ ...styles.input, width: "70px" }}
                      value={editing.auto_rag_limit}
                      onChange={(e) => setEditing({ ...editing, auto_rag_limit: parseInt(e.target.value) || 5 })}
                    />
                  </div>
                )}
              </div>
            </div>

            <div style={{ ...styles.field, ...styles.formFull }}>
              <label style={styles.label}>System Prompt</label>
              <textarea
                style={{ ...styles.input, minHeight: "100px", resize: "vertical" }}
                value={editing.system_prompt}
                onChange={(e) => setEditing({ ...editing, system_prompt: e.target.value })}
              />
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

            <div style={{ ...styles.field, ...styles.formFull }}>
              <label style={styles.label}>
                Workspace Mounts{" "}
                <button style={styles.smallButton} onClick={addMount}>+ Add</button>
              </label>
              {editing.workspace_mounts?.map((mount, i) => (
                <div key={i} style={{ marginBottom: "8px", display: "flex", flexDirection: "column", gap: "4px" }}>
                  <div style={styles.mountRow}>
                    <input
                      style={{ ...styles.input, flex: 1 }}
                      value={mount.host_path}
                      onChange={(e) => updateMount(i, "host_path", e.target.value)}
                      placeholder="Host path"
                    />
                    <button style={styles.smallButton} onClick={() => setBrowsingMountIndex(i)} title="Browse">📂</button>
                    <span style={{ color: "#8888a0", fontSize: "0.85rem" }}>→</span>
                    <input
                      style={{ ...styles.input, flex: 1 }}
                      value={mount.container_path || `/workspace/${mount.mount_name}`}
                      onChange={(e) => updateMount(i, "container_path", e.target.value)}
                      placeholder="Container path (e.g. /workspace/myproject)"
                    />
                    <label style={styles.checkboxLabel}>
                      <input type="checkbox" checked={mount.readonly} onChange={(e) => updateMount(i, "readonly", e.target.checked)} style={styles.checkbox} />
                      RO
                    </label>
                    <button style={styles.dangerSmall} onClick={() => removeMount(i)}>X</button>
                  </div>
                </div>
              ))}
            </div>

            {browsingMountIndex !== null && (
              <DirBrowser
                onSelect={(path) => {
                  const idx = browsingMountIndex;
                  const name = path.split("/").filter(Boolean).pop() || "";
                  setEditing((prev) => {
                    if (!prev) return prev;
                    const mounts = [...prev.workspace_mounts];
                    mounts[idx] = {
                      ...mounts[idx],
                      host_path: path,
                      mount_name: mounts[idx].mount_name || name,
                      container_path: mounts[idx].container_path || `/workspace/${mounts[idx].mount_name || name}`,
                    };
                    return { ...prev, workspace_mounts: mounts };
                  });
                  setBrowsingMountIndex(null);
                }}
                onClose={() => setBrowsingMountIndex(null)}
              />
            )}

            <div style={{ ...styles.field, ...styles.formFull }}>
              <label style={styles.label}>Prompt Fragments</label>
              <div style={styles.checkboxGrid}>
                {allFragments.filter(f => f.is_active).map((frag) => {
                  const attached = isNew
                    ? pendingFragmentIds.has(frag.id)
                    : agentFragments.some(af => af.id === frag.id);
                  const catColor: Record<string, string> = { behavior: "#6cffa0", tools: "#6c8aff", safety: "#ff6c8a", context: "#ffcc44" };
                  return (
                    <label key={frag.id} style={styles.checkboxLabel}>
                      <input
                        type="checkbox"
                        checked={attached}
                        onChange={() => {
                          if (isNew) {
                            const next = new Set(pendingFragmentIds);
                            if (next.has(frag.id)) next.delete(frag.id);
                            else next.add(frag.id);
                            setPendingFragmentIds(next);
                          } else {
                            toggleAgentFragment(editing.id, frag.id, attached);
                          }
                        }}
                        style={styles.checkbox}
                      />
                      <span style={{ color: catColor[frag.category] || "#888", fontSize: "0.7rem", marginRight: "4px" }}>●</span>
                      {frag.display_name}
                    </label>
                  );
                })}
              </div>
              <div style={{ fontSize: "0.8rem", color: "#5a5a6e", marginTop: "4px" }}>
                Manage fragments in the Prompts tab
              </div>
            </div>

            {!isNew && !editing.is_default && (
              <div style={styles.buttonRow}>
                <button style={styles.secondaryButton} onClick={() => setDefault(editing.id)}>
                  Set Default
                </button>
                <button style={styles.dangerButton} onClick={() => deleteAgent(editing.id)}>
                  Delete
                </button>
              </div>
            )}
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
  topBar: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    padding: "12px 0", borderBottom: "1px solid #1e1e2e", marginBottom: "16px", flexShrink: 0,
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
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "16px",
  },
  formFull: { gridColumn: "1 / -1" },
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
