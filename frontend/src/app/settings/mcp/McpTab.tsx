import React, { useState, useEffect } from "react";
import { useMcpServers, useAgents, useSpacetimeConnection, callReducer } from "@/hooks/useSpacetimeDB";
import { type McpServerRow, type AgentRow } from "@/lib/spacetimedb-client";

function generateId(): string {
  return crypto.randomUUID().replace(/-/g, '');
}

interface EnvVar {
  key: string;
  value: string;
  masked: boolean;
}

interface McpServerForm {
  id: string;
  name: string;
  command: string;
  args: string[];
  env: EnvVar[];
  enabled: boolean;
  agentId: string | null;
}

function parseArgs(argsJson: string): string[] {
  try { return JSON.parse(argsJson); } catch { return []; }
}

function parseEnv(envJson: string): EnvVar[] {
  try {
    const obj = JSON.parse(envJson);
    return Object.entries(obj).map(([key, value]) => ({ key, value: value as string, masked: true }));
  } catch { return []; }
}

function serverToForm(s: McpServerRow): McpServerForm {
  return {
    id: s.id,
    name: s.name,
    command: s.command,
    args: parseArgs(s.args),
    env: parseEnv(s.env),
    enabled: s.enabled,
    agentId: s.agentId,
  };
}

function StatusBadge({ status, lastError }: { status: string; lastError?: string | null }) {
  const colors: Record<string, { bg: string; text: string; dot: string }> = {
    connected: { bg: "#1a3a1a", text: "#4ade80", dot: "#22c55e" },
    connecting: { bg: "#3a3a1a", text: "#facc15", dot: "#eab308" },
    error: { bg: "#3a1a1a", text: "#ff6c8a", dot: "#ef4444" },
    stopped: { bg: "#2a2a3e", text: "#8888a0", dot: "#6b7280" },
    disabled: { bg: "#2a2a3e", text: "#5a5a6e", dot: "#4b5563" },
  };
  const c = colors[status] || colors.stopped;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
      <span style={{
        width: "8px", height: "8px", borderRadius: "50%",
        backgroundColor: c.dot, display: "inline-block",
        boxShadow: status === "connected" ? `0 0 6px ${c.dot}` : "none",
      }} />
      <span style={{
        fontSize: "0.75rem", color: c.text,
        backgroundColor: c.bg, padding: "2px 8px",
        borderRadius: "4px", fontWeight: 500,
      }}>
        {status}
      </span>
      {status === "error" && lastError && (
        <span style={{ fontSize: "0.7rem", color: "#ff6c8a", maxWidth: "300px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={lastError}>
          {lastError}
        </span>
      )}
    </div>
  );
}

export default function McpTab() {
  const servers = useMcpServers();
  const agents = useAgents();
  const { connected } = useSpacetimeConnection();
  const [editing, setEditing] = useState<McpServerForm | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [serverStatuses, setServerStatuses] = useState<Record<string, any>>({});
  const [testResult, setTestResult] = useState<{
    success: boolean;
    status: string;
    tools: { name: string; description: string }[];
    connect_time_ms: number;
    error: string | null;
  } | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchStatus = async () => {
      try {
        const res = await fetch("/api/v1/mcp/servers/status");
        if (res.ok) {
          const data = await res.json();
          const map: Record<string, any> = {};
          for (const s of data.servers || []) {
            map[s.server] = s;
          }
          if (!cancelled) setServerStatuses(map);
        }
      } catch {}
    };
    fetchStatus();
    const interval = setInterval(fetchStatus, 15000);
    return () => { cancelled = true; clearInterval(interval); };
  }, []);

  const getAgentName = (agentId: string | null): string => {
    if (!agentId) return "Global (all agents)";
    const agent = agents.find((a: AgentRow) => a.id === agentId);
    return agent ? agent.displayName || agent.name : agentId;
  };

  const startCreate = () => {
    setEditing({
      id: "",
      name: "",
      command: "",
      args: [],
      env: [],
      enabled: true,
      agentId: null,
    });
    setIsNew(true);
    setMsg("");
    setTestResult(null);
  };

  const startEdit = (server: McpServerRow) => {
    setEditing(serverToForm(server));
    setIsNew(false);
    setMsg("");
    setTestResult(null);
  };

  const handleSave = () => {
    if (!editing) return;
    if (!editing.name.trim() || !editing.command.trim()) {
      setMsg("Name and command are required.");
      return;
    }

    const argsJson = JSON.stringify(editing.args);
    const envJson = JSON.stringify(
      Object.fromEntries(editing.env.map(e => [e.key, e.value]))
    );

    const ok = isNew
      ? callReducer(conn => conn.reducers.addMcpServer({
          id: generateId(),
          name: editing.name,
          command: editing.command,
          args: argsJson,
          env: envJson,
          agentId: editing.agentId || undefined,
        }))
      : callReducer(conn => conn.reducers.updateMcpServer({
          id: editing.id,
          name: editing.name,
          command: editing.command,
          args: argsJson,
          env: envJson,
          enabled: editing.enabled,
          agentId: editing.agentId || undefined,
        }));

    if (!ok) { setMsg("Not connected to database. Please wait and try again."); return; }
    setEditing(null);
    setMsg("Saved.");
  };

  const handleDelete = (id: string) => {
    const ok = callReducer(conn => conn.reducers.deleteMcpServer({ id }));
    if (!ok) { setMsg("Not connected to database."); return; }
    setMsg("Deleted.");
    setEditing(null);
    setConfirmDelete(null);
  };

  const handleToggle = (server: McpServerRow) => {
    callReducer(conn => conn.reducers.updateMcpServer({
      id: server.id,
      name: server.name,
      command: server.command,
      args: server.args,
      env: server.env,
      enabled: !server.enabled,
      agentId: server.agentId || undefined,
    }));
  };

  const handleTestConnection = async () => {
    if (!editing) return;
    setTesting(true);
    setTestResult(null);
    try {
      const res = await fetch("/api/v1/mcp/servers/test", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: editing.name || "test",
          command: editing.command,
          args: editing.args,
          env: Object.fromEntries(editing.env.map(e => [e.key, e.value])),
        }),
      });
      const data = await res.json();
      setTestResult(data);
    } catch (e: any) {
      setTestResult({ success: false, status: "error", tools: [], connect_time_ms: 0, error: e.message });
    } finally {
      setTesting(false);
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", minHeight: 0 }}>
      <div style={styles.topBar}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
          {editing ? (isNew ? "Add MCP Server" : `Editing: ${editing.name}`) : "MCP Servers"}
        </h2>
        <div style={{ display: "flex", gap: "8px" }}>
          {editing ? (
            <>
              <button style={styles.button} onClick={handleSave}>Save</button>
              <button style={styles.secondaryButton} onClick={() => { setEditing(null); setMsg(""); setTestResult(null); }}>Cancel</button>
            </>
          ) : (
            <button style={styles.button} onClick={startCreate}>+ Add Server</button>
          )}
        </div>
      </div>

      {msg && <div style={styles.msg}>{msg}</div>}

      <div style={{ flex: 1, overflowY: "auto" }}>
        {!editing ? (
          <>
            <div style={styles.cardGrid}>
              {servers.map((server) => {
                const st = serverStatuses[server.name];
                const statusStr = st?.status || (server.enabled ? "stopped" : "disabled");
                const toolCount = st?.tool_count || 0;
                return (
                  <div key={server.id} style={styles.card}>
                    <div style={styles.cardHeader}>
                      <span style={styles.cardName}>{server.name}</span>
                      <button
                        style={{
                          ...styles.smallButton,
                          backgroundColor: server.enabled ? "#1a3a1a" : "#3a1a1a",
                          color: server.enabled ? "#6cffa0" : "#ff6c8a",
                        }}
                        onClick={() => handleToggle(server)}
                      >
                        {server.enabled ? "Enabled" : "Disabled"}
                      </button>
                    </div>
                    <div style={{ marginBottom: "8px" }}>
                      <StatusBadge status={statusStr} lastError={st?.last_error} />
                    </div>
                    <div style={styles.cardMeta}>Command: {server.command} {parseArgs(server.args).join(" ")}</div>
                    <div style={styles.cardMeta}>Scope: {getAgentName(server.agentId)}</div>
                    {toolCount > 0 && (
                      <div style={styles.cardMeta}>Tools: {toolCount} available</div>
                    )}
                    {parseEnv(server.env).length > 0 && (
                      <div style={styles.cardMeta}>
                        Env: {parseEnv(server.env).map(e => `${e.key}=\u2022\u2022\u2022\u2022\u2022\u2022`).join(", ")}
                      </div>
                    )}
                    <div style={{ display: "flex", gap: "8px", marginTop: "12px" }}>
                      <button style={styles.smallButton} onClick={() => startEdit(server)}>Edit</button>
                      {confirmDelete === server.id ? (
                        <>
                          <span style={{ color: "#ff6c8a", fontSize: "0.8rem", alignSelf: "center" }}>Delete?</span>
                          <button style={styles.dangerSmall} onClick={() => handleDelete(server.id)}>Yes</button>
                          <button style={styles.smallButton} onClick={() => setConfirmDelete(null)}>No</button>
                        </>
                      ) : (
                        <button style={styles.dangerSmall} onClick={() => setConfirmDelete(server.id)}>Delete</button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
            {servers.length === 0 && (
              <div style={{ color: "#8888a0", fontSize: "0.9rem", padding: "20px 0" }}>
                No MCP servers configured. Click &quot;+ Add Server&quot; to get started.
              </div>
            )}
            <div style={{ color: "#5a5a6e", fontSize: "0.8rem", marginTop: "16px", lineHeight: "1.4" }}>
              Global servers are available to all agents. Agent-specific servers are only visible to that agent.
            </div>
          </>
        ) : (
          <div style={styles.form}>
            <div style={styles.field}>
              <label style={styles.label}>Name</label>
              <input
                style={styles.input}
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="e.g. solidtime"
              />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Command</label>
              <input
                style={styles.input}
                value={editing.command}
                onChange={(e) => setEditing({ ...editing, command: e.target.value })}
                placeholder="e.g. npx, uvx, node"
              />
            </div>

            <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
              <label style={styles.label}>
                Arguments{" "}
                <button
                  style={styles.smallButton}
                  onClick={() => setEditing({ ...editing, args: [...editing.args, ""] })}
                >+ Add</button>
              </label>
              {editing.args.map((arg, i) => (
                <div key={i} style={{ display: "flex", gap: "8px", marginBottom: "4px", alignItems: "center" }}>
                  <span style={{ color: "#5a5a6e", fontSize: "0.8rem", width: "24px" }}>[{i}]</span>
                  <input
                    style={{ ...styles.input, flex: 1 }}
                    value={arg}
                    onChange={(e) => {
                      const args = [...editing.args];
                      args[i] = e.target.value;
                      setEditing({ ...editing, args });
                    }}
                  />
                  <button
                    style={styles.dangerSmall}
                    onClick={() => setEditing({ ...editing, args: editing.args.filter((_, j) => j !== i) })}
                  >X</button>
                </div>
              ))}
            </div>

            <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
              <label style={styles.label}>
                Environment Variables{" "}
                <button
                  style={styles.smallButton}
                  onClick={() => setEditing({ ...editing, env: [...editing.env, { key: "", value: "", masked: true }] })}
                >+ Add</button>
              </label>
              {editing.env.map((envVar, i) => (
                <div key={i} style={{ display: "flex", gap: "8px", marginBottom: "4px", alignItems: "center" }}>
                  <input
                    style={{ ...styles.input, flex: 1 }}
                    value={envVar.key}
                    onChange={(e) => {
                      const env = [...editing.env];
                      env[i] = { ...env[i], key: e.target.value };
                      setEditing({ ...editing, env });
                    }}
                    placeholder="KEY"
                  />
                  <input
                    style={{ ...styles.input, flex: 2 }}
                    type={envVar.masked ? "password" : "text"}
                    value={envVar.value}
                    onChange={(e) => {
                      const env = [...editing.env];
                      env[i] = { ...env[i], value: e.target.value };
                      setEditing({ ...editing, env });
                    }}
                    placeholder="value"
                  />
                  <button
                    style={styles.smallButton}
                    onClick={() => {
                      const env = [...editing.env];
                      env[i] = { ...env[i], masked: !env[i].masked };
                      setEditing({ ...editing, env });
                    }}
                    title={envVar.masked ? "Show value" : "Hide value"}
                  >{envVar.masked ? "\u{1F441}" : "\u{1F648}"}</button>
                  <button
                    style={styles.dangerSmall}
                    onClick={() => setEditing({ ...editing, env: editing.env.filter((_, j) => j !== i) })}
                  >X</button>
                </div>
              ))}
            </div>

            <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
              <label style={styles.label}>Agent Scope</label>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                <label style={styles.checkboxLabel}>
                  <input
                    type="radio"
                    name="agentScope"
                    checked={editing.agentId === null}
                    onChange={() => setEditing({ ...editing, agentId: null })}
                    style={{ accentColor: "#6c8aff" }}
                  />
                  Global — available to all agents
                </label>
                <label style={styles.checkboxLabel}>
                  <input
                    type="radio"
                    name="agentScope"
                    checked={editing.agentId !== null}
                    onChange={() => setEditing({ ...editing, agentId: agents.length > 0 ? agents[0].id : "" })}
                    style={{ accentColor: "#6c8aff" }}
                  />
                  Specific agent:
                  {editing.agentId !== null && (
                    <select
                      style={{ ...styles.select, width: "auto", marginLeft: "8px" }}
                      value={editing.agentId || ""}
                      onChange={(e) => setEditing({ ...editing, agentId: e.target.value })}
                    >
                      {agents.map((agent: AgentRow) => (
                        <option key={agent.id} value={agent.id}>
                          {agent.displayName || agent.name}
                        </option>
                      ))}
                    </select>
                  )}
                </label>
              </div>
            </div>

            {!isNew && (
              <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
                <label style={styles.checkboxLabel}>
                  <input
                    type="checkbox"
                    checked={editing.enabled}
                    onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })}
                    style={{ accentColor: "#6c8aff" }}
                  />
                  Enabled
                </label>
              </div>
            )}

            {/* Test Connection */}
            <div style={{ ...styles.field, gridColumn: "1 / -1" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                <button
                  style={{
                    ...styles.secondaryButton,
                    opacity: !editing.command ? 0.5 : 1,
                    cursor: !editing.command ? "not-allowed" : "pointer",
                  }}
                  onClick={handleTestConnection}
                  disabled={!editing.command || testing}
                >
                  {testing ? "Testing..." : "Test Connection"}
                </button>
                {testResult && (
                  <StatusBadge status={testResult.status} lastError={testResult.error} />
                )}
                {testResult?.success && (
                  <span style={{ fontSize: "0.8rem", color: "#4ade80" }}>
                    {testResult.connect_time_ms}ms
                  </span>
                )}
              </div>
              {testResult?.success && testResult.tools.length > 0 && (
                <div style={{ marginTop: "12px", backgroundColor: "#1e1e2e", borderRadius: "8px", padding: "12px" }}>
                  <div style={{ fontSize: "0.8rem", color: "#8888a0", marginBottom: "8px" }}>
                    Discovered {testResult.tools.length} tools:
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
                    {testResult.tools.map((tool) => (
                      <span
                        key={tool.name}
                        style={{
                          fontSize: "0.75rem",
                          backgroundColor: "#2a2a3e",
                          color: "#6c8aff",
                          padding: "3px 8px",
                          borderRadius: "4px",
                        }}
                        title={tool.description}
                      >
                        {tool.name}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {testResult && !testResult.success && testResult.error && (
                <div style={{ marginTop: "8px", fontSize: "0.8rem", color: "#ff6c8a", backgroundColor: "#3a1a1a", padding: "8px 12px", borderRadius: "8px" }}>
                  {testResult.error}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  topBar: {
    display: "flex", justifyContent: "space-between", alignItems: "center",
    padding: "12px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", marginBottom: "16px", flexShrink: 0,
  },
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
  },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  cardName: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8" },
  cardMeta: { fontSize: "0.8rem", color: "#8888a0", marginTop: "4px" },
  form: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "24px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: "16px",
  },
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
  },
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
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    cursor: "pointer",
  },
  smallButton: {
    backgroundColor: "#2a2a3e",
    color: "#6c8aff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "4px",
    padding: "4px 8px",
    fontSize: "0.75rem",
    cursor: "pointer",
    marginLeft: "8px",
  },
  dangerSmall: {
    backgroundColor: "#3a1a1a",
    color: "#ff6c8a",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "4px",
    padding: "4px 8px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  checkboxLabel: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
};
