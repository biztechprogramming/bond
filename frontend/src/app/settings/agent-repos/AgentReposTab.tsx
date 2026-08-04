import React, { useState, useEffect, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";

/** Design Doc 113 — per-agent repo management. Embedded inside the agent
 * edit screen; can also render standalone with its own agent picker. */

interface Agent {
  id: string;
  name: string;
  display_name: string;
  is_default: boolean;
}

interface AgentRepo {
  id: string;
  agent_id: string;
  url: string;
  name: string;
  default_branch: string;
  active_branch: string;
  credential_id: string;
  last_synced_at: number;
}

interface GitCredential {
  id: string;
  name: string;
  auth_type: string;
  host_pattern: string;
}

interface RepoForm {
  url: string;
  name: string;
  default_branch: string;
  active_branch: string;
  credential_id: string;
}

const empty = (): RepoForm => ({
  url: "",
  name: "",
  default_branch: "main",
  active_branch: "",
  credential_id: "",
});

function deriveName(url: string): string {
  let s = url.trim().replace(/\/$/, "");
  if (s.endsWith(".git")) s = s.slice(0, -4);
  for (const sep of ["/", ":"]) {
    const idx = s.lastIndexOf(sep);
    if (idx >= 0) s = s.slice(idx + 1);
  }
  // decode percent-encoding (e.g. Approval%20Ace → Approval Ace)
  try { s = decodeURIComponent(s); } catch { /* leave as-is if malformed */ }
  // replace invalid chars (spaces, %, etc.) with hyphens
  s = s.replace(/[^A-Za-z0-9_.-]+/g, "-");
  // strip leading chars that would fail mount name validation (e.g. dots, dashes)
  s = s.replace(/^[^A-Za-z0-9]+/, "");
  return s || "repo";
}

interface Props {
  agentId?: string;
}

export default function AgentReposTab({ agentId }: Props = {}) {
  const embedded = !!agentId;
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>(agentId || "");
  const [repos, setRepos] = useState<AgentRepo[]>([]);
  const [creds, setCreds] = useState<GitCredential[]>([]);
  const [editing, setEditing] = useState<RepoForm | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [hoveredCard, setHoveredCard] = useState<string | null>(null);

  const fetchAgents = useCallback(async () => {
    try {
      const res = await apiFetch(`${BACKEND_API}/agents`);
      if (res.ok) {
        const data: Agent[] = await res.json();
        setAgents(data);
        if (data.length && !selectedAgentId) {
          const def = data.find((a) => a.is_default) || data[0];
          setSelectedAgentId(def.id);
        }
      }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fetchCreds = useCallback(async () => {
    try {
      const res = await apiFetch(`${BACKEND_API}/git-credentials`);
      if (res.ok) setCreds(await res.json());
    } catch {}
  }, []);

  const fetchRepos = useCallback(async (id: string) => {
    if (!id) {
      setRepos([]);
      return;
    }
    try {
      const res = await apiFetch(`${BACKEND_API}/agents/${id}/repos`);
      if (res.ok) setRepos(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    if (!embedded) fetchAgents();
    fetchCreds();
  }, [embedded, fetchAgents, fetchCreds]);

  useEffect(() => {
    if (agentId !== undefined) setSelectedAgentId(agentId);
  }, [agentId]);

  useEffect(() => {
    fetchRepos(selectedAgentId);
  }, [selectedAgentId, fetchRepos]);

  // Auto-clear messages
  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(""), 3000);
    return () => clearTimeout(t);
  }, [msg]);

  const startCreate = () => {
    setEditing(empty());
    setEditingId(null);
    setIsNew(true);
    setMsg("");
  };

  const startEdit = (r: AgentRepo) => {
    setEditing({
      url: r.url,
      name: r.name,
      default_branch: r.default_branch,
      active_branch: r.active_branch,
      credential_id: r.credential_id,
    });
    setEditingId(r.id);
    setIsNew(false);
    setMsg("");
  };

  const cancel = () => {
    setEditing(null);
    setEditingId(null);
    setIsNew(false);
    setMsg("");
  };

  const onUrlChange = (url: string) => {
    if (!editing) return;
    const update: RepoForm = { ...editing, url };
    if (!editing.name.trim() && url.trim()) {
      update.name = deriveName(url);
    }
    setEditing(update);
  };

  const save = async () => {
    if (!editing || !selectedAgentId) return;
    if (!editing.url.trim()) {
      setMsg("Error: URL is required.");
      return;
    }
    const payload = {
      url: editing.url,
      name: editing.name || deriveName(editing.url),
      default_branch: editing.default_branch || "main",
      active_branch: editing.active_branch,
      credential_id: editing.credential_id,
    };
    const url = isNew
      ? `${BACKEND_API}/agents/${selectedAgentId}/repos`
      : `${BACKEND_API}/agents/${selectedAgentId}/repos/${editingId}`;
    const method = isNew ? "POST" : "PUT";
    const res = await apiFetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      setMsg(isNew ? "Repo added." : "Repo updated.");
      await fetchRepos(selectedAgentId);
      cancel();
    } else {
      const text = await res.text();
      setMsg(`Error: ${text}`);
    }
  };

  const remove = async (id: string) => {
    const res = await apiFetch(`${BACKEND_API}/agents/${selectedAgentId}/repos/${id}`, {
      method: "DELETE",
    });
    if (res.ok) {
      setMsg("Repo removed.");
      await fetchRepos(selectedAgentId);
      setConfirmDelete(null);
    } else {
      const text = await res.text();
      setMsg(`Error: ${text}`);
      setConfirmDelete(null);
    }
  };

  // ── Field styles ─────────────────────────────────────────────
  const fieldLabel: React.CSSProperties = {
    display: "block",
    fontSize: "0.78rem",
    color: "#8888a0",
    marginBottom: 6,
    fontWeight: 500,
  };
  const fieldInput: React.CSSProperties = {
    width: "100%",
    background: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: 8,
    padding: "9px 12px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    outline: "none",
    boxSizing: "border-box",
  };

  return (
    <div style={{ gridColumn: "1 / -1" }}>
      {/* Section header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16, gap: 12, flexWrap: "wrap" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span style={{ fontSize: "0.95rem", fontWeight: 600, color: "#8888a0", letterSpacing: "0.02em" }}>Repos</span>
          <span style={{ fontSize: "0.8rem", color: "#5a5a6e", background: "#1e1e2e", padding: "2px 8px", borderRadius: 10 }}>{repos.length}</span>
        </div>

        {!embedded && (
          <select
            value={selectedAgentId}
            onChange={(e) => setSelectedAgentId(e.target.value)}
            style={{ ...fieldInput, width: "auto" }}
          >
            <option value="">— Pick agent —</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name || a.name}{a.is_default ? " (default)" : ""}
              </option>
            ))}
          </select>
        )}

        {selectedAgentId && repos.length > 0 && !editing && (
          <button onClick={startCreate} style={{
            display: "flex", alignItems: "center", gap: 6,
            background: "rgba(108,138,255,0.12)", color: "#6c8aff",
            border: "1px solid transparent", borderRadius: 6, padding: "6px 14px",
            fontSize: "0.82rem", fontWeight: 600, cursor: "pointer", transition: "all 0.15s",
          }}>
            <span style={{ fontSize: "1rem", lineHeight: 1 }}>+</span> Add Repo
          </button>
        )}
      </div>

      {/* Toast */}
      {msg && (
        <div style={{ fontSize: "0.8rem", color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0", marginBottom: 12 }}>
          {msg}
        </div>
      )}

      {/* Edit form */}
      {editing && (
        <div style={{
          background: "#12121a", border: "1px solid #2a2a3e", borderRadius: 10,
          padding: 16, marginBottom: 12,
        }}>
          <div style={{ fontSize: "0.88rem", fontWeight: 600, color: "#e0e0e8", marginBottom: 12 }}>
            {isNew ? "New Repo" : "Edit Repo"}
          </div>
          <div style={{ display: "grid", gap: 12 }}>
            <div>
              <label style={fieldLabel}>Clone URL</label>
              <input
                type="text"
                value={editing.url}
                onChange={(e) => onUrlChange(e.target.value)}
                placeholder="git@github.com:foo/bar.git or https://github.com/foo/bar.git"
                style={fieldInput}
              />
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <label style={fieldLabel}>Mount name</label>
                <input
                  type="text"
                  value={editing.name}
                  onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                  placeholder="defaults to repo basename"
                  style={fieldInput}
                />
              </div>
              <div>
                <label style={fieldLabel}>Default branch</label>
                <input
                  type="text"
                  value={editing.default_branch}
                  onChange={(e) => setEditing({ ...editing, default_branch: e.target.value })}
                  placeholder="main"
                  style={fieldInput}
                />
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div>
                <label style={fieldLabel}>Active branch (optional)</label>
                <input
                  type="text"
                  value={editing.active_branch}
                  onChange={(e) => setEditing({ ...editing, active_branch: e.target.value })}
                  placeholder="e.g. fix/some-bug"
                  style={fieldInput}
                />
              </div>
              <div>
                <label style={fieldLabel}>Credential override</label>
                <select
                  value={editing.credential_id}
                  onChange={(e) => setEditing({ ...editing, credential_id: e.target.value })}
                  style={fieldInput}
                >
                  <option value="">— Use default for host —</option>
                  {creds.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name} ({c.host_pattern})
                    </option>
                  ))}
                </select>
              </div>
            </div>
            <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 4 }}>
              <button onClick={cancel} style={{
                background: "none", border: "1px solid #2a2a3e", color: "#8888a0",
                borderRadius: 6, padding: "8px 16px", fontSize: "0.82rem", cursor: "pointer",
              }}>Cancel</button>
              <button onClick={save} style={{
                background: "#6c8aff", color: "#fff", border: "none",
                borderRadius: 6, padding: "8px 18px", fontSize: "0.82rem", fontWeight: 600, cursor: "pointer",
              }}>Save</button>
            </div>
          </div>
        </div>
      )}

      {/* Empty state */}
      {selectedAgentId && repos.length === 0 && !editing && (
        <div style={{
          textAlign: "center", padding: "40px 20px",
          border: "1px dashed #2a2a3e", borderRadius: 10, background: "#12121a",
        }}>
          <div style={{ fontSize: "2rem", marginBottom: 12, opacity: 0.4 }}>&#x1f4c1;</div>
          <p style={{ color: "#5a5a6e", fontSize: "0.85rem", marginBottom: 16, lineHeight: 1.5 }}>
            No repos yet.<br />
            Add a clone URL and the agent will get it at <code style={{ background: "#1e1e2e", padding: "1px 6px", borderRadius: 3 }}>/workspace/&#123;name&#125;</code>.
          </p>
          <button onClick={startCreate} style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            background: "#6c8aff", color: "#fff", border: "none", borderRadius: 6,
            padding: "9px 20px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer",
          }}>
            <span>+</span> Add Repo
          </button>
        </div>
      )}

      {/* Empty state — no agent selected (standalone only) */}
      {!selectedAgentId && (
        <div style={{
          textAlign: "center", padding: "30px 20px",
          color: "#5a5a6e", fontSize: "0.85rem",
        }}>
          Pick an agent above to manage its repos.
        </div>
      )}

      {/* Repo cards */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {repos.map((r) => {
          const cred = creds.find((c) => c.id === r.credential_id);
          const branch = r.active_branch || r.default_branch;
          const isHover = hoveredCard === r.id;
          return (
            <div
              key={r.id}
              onMouseEnter={() => setHoveredCard(r.id)}
              onMouseLeave={() => setHoveredCard(null)}
              style={{
                display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
                background: "#12121a", border: `1px solid ${isHover ? "#3a3a4e" : "#2a2a3e"}`,
                borderRadius: 10, padding: "14px 16px", transition: "border-color 0.15s",
              }}
            >
              <div style={{
                width: 34, height: 34, borderRadius: 8, flexShrink: 0,
                background: "#1e1e2e", display: "flex", alignItems: "center", justifyContent: "center",
                fontSize: "1.1rem",
              }} aria-hidden>
                &#x1f4e6;
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: "0.9rem", fontWeight: 600, color: "#e0e0e8", marginBottom: 3, display: "flex", alignItems: "center", gap: 8 }}>
                  {r.name}
                  <span style={{
                    fontSize: "0.72rem", color: "#8888a0", background: "#1e1e2e",
                    padding: "1px 7px", borderRadius: 4, fontWeight: 500,
                  }}>{branch}</span>
                </div>
                <div style={{ fontSize: "0.76rem", color: "#5a5a6e", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  <code>{r.url}</code>
                  {cred && <span> &middot; {cred.name}</span>}
                </div>
              </div>
              <button
                onClick={() => startEdit(r)}
                style={{
                  background: "none", border: "none", color: "#8888a0", cursor: "pointer",
                  padding: 6, borderRadius: 6, fontSize: "0.8rem", flexShrink: 0,
                  opacity: isHover ? 1 : 0.5, transition: "opacity 0.15s",
                }}
              >Edit</button>
              {confirmDelete === r.id ? (
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button
                    onClick={() => remove(r.id)}
                    style={{
                      background: "rgba(255,108,138,0.12)", color: "#ff6c8a",
                      border: "none", borderRadius: 6, padding: "5px 10px",
                      fontSize: "0.76rem", fontWeight: 600, cursor: "pointer",
                    }}
                  >Confirm</button>
                  <button
                    onClick={() => setConfirmDelete(null)}
                    style={{
                      background: "none", border: "1px solid #2a2a3e", color: "#8888a0",
                      borderRadius: 6, padding: "5px 10px", fontSize: "0.76rem", cursor: "pointer",
                    }}
                  >X</button>
                </div>
              ) : (
                <button
                  onClick={() => setConfirmDelete(r.id)}
                  title="Remove"
                  aria-label={`Remove ${r.name}`}
                  style={{
                    background: "none", border: "none", color: "#5a5a6e", cursor: "pointer",
                    padding: 6, borderRadius: 6, fontSize: "1rem", lineHeight: 1, flexShrink: 0,
                    opacity: isHover ? 1 : 0.15, transition: "all 0.15s",
                  }}
                >&#x2715;</button>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
