import React, { useState, useEffect, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";

/** Design Doc 113 — per-agent repo management. */

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
  return s || "repo";
}

export default function AgentReposTab() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [repos, setRepos] = useState<AgentRepo[]>([]);
  const [creds, setCreds] = useState<GitCredential[]>([]);
  const [editing, setEditing] = useState<RepoForm | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

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

  const fetchRepos = useCallback(async (agentId: string) => {
    if (!agentId) {
      setRepos([]);
      return;
    }
    try {
      const res = await apiFetch(`${BACKEND_API}/agents/${agentId}/repos`);
      if (res.ok) setRepos(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchAgents();
    fetchCreds();
  }, [fetchAgents, fetchCreds]);

  useEffect(() => {
    fetchRepos(selectedAgentId);
  }, [selectedAgentId, fetchRepos]);

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
    // Auto-fill name from URL basename if name is currently empty
    const update: RepoForm = { ...editing, url };
    if (!editing.name.trim() && url.trim()) {
      update.name = deriveName(url);
    }
    setEditing(update);
  };

  const save = async () => {
    if (!editing || !selectedAgentId) return;
    if (!editing.url.trim()) {
      setMsg("URL is required.");
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
      await fetchRepos(selectedAgentId);
      cancel();
    } else {
      const text = await res.text();
      setMsg(`Save failed: ${text}`);
    }
  };

  const remove = async (id: string) => {
    const res = await apiFetch(`${BACKEND_API}/agents/${selectedAgentId}/repos/${id}`, {
      method: "DELETE",
    });
    if (res.ok) {
      await fetchRepos(selectedAgentId);
      setConfirmDelete(null);
    } else {
      const text = await res.text();
      setMsg(`Delete failed: ${text}`);
      setConfirmDelete(null);
    }
  };

  return (
    <div style={{ padding: "16px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
        <div>
          <h2 style={{ margin: 0 }}>Agent Repos</h2>
          <p style={{ margin: "4px 0 0", color: "#666", fontSize: "13px" }}>
            Per-agent git clones. Each repo is cloned into a Docker volume mounted at <code>/workspace/&#123;name&#125;</code>.
          </p>
        </div>
        <div>
          <label style={{ marginRight: "8px" }}>Agent:</label>
          <select value={selectedAgentId} onChange={(e) => setSelectedAgentId(e.target.value)}>
            <option value="">— Pick one —</option>
            {agents.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name || a.name}{a.is_default ? " (default)" : ""}
              </option>
            ))}
          </select>
        </div>
      </div>

      {msg && <div style={{ color: "#b91c1c", marginBottom: "8px" }}>{msg}</div>}

      {selectedAgentId && !editing && (
        <button onClick={startCreate} style={{ marginBottom: "12px" }}>+ Add repo</button>
      )}

      {editing && (
        <div style={{ border: "1px solid #ccc", padding: "16px", marginBottom: "16px", borderRadius: "4px" }}>
          <h3 style={{ marginTop: 0 }}>{isNew ? "New repo" : "Edit repo"}</h3>
          <div style={{ display: "grid", gap: "10px", maxWidth: "640px" }}>
            <label>
              Clone URL
              <input
                type="text"
                value={editing.url}
                onChange={(e) => onUrlChange(e.target.value)}
                placeholder="git@github.com:foo/bar.git or https://github.com/foo/bar.git"
                style={{ display: "block", width: "100%" }}
              />
            </label>
            <label>
              Mount name (defaults to repo basename)
              <input
                type="text"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="will mount at /workspace/<name>"
                style={{ display: "block", width: "100%" }}
              />
            </label>
            <label>
              Default branch
              <input
                type="text"
                value={editing.default_branch}
                onChange={(e) => setEditing({ ...editing, default_branch: e.target.value })}
                placeholder="main"
                style={{ display: "block", width: "100%" }}
              />
            </label>
            <label>
              Active branch (leave empty to start on default)
              <input
                type="text"
                value={editing.active_branch}
                onChange={(e) => setEditing({ ...editing, active_branch: e.target.value })}
                placeholder="e.g. fix/some-bug"
                style={{ display: "block", width: "100%" }}
              />
            </label>
            <label>
              Credential override (optional — defaults to host_pattern match)
              <select
                value={editing.credential_id}
                onChange={(e) => setEditing({ ...editing, credential_id: e.target.value })}
                style={{ display: "block", width: "100%" }}
              >
                <option value="">— Use default for host —</option>
                {creds.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.name} ({c.host_pattern})
                  </option>
                ))}
              </select>
            </label>
            <div style={{ display: "flex", gap: "8px", marginTop: "8px" }}>
              <button onClick={save}>Save</button>
              <button onClick={cancel}>Cancel</button>
            </div>
          </div>
        </div>
      )}

      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "14px" }}>
        <thead>
          <tr style={{ background: "#f5f5f5", textAlign: "left" }}>
            <th style={{ padding: "8px" }}>Name</th>
            <th style={{ padding: "8px" }}>URL</th>
            <th style={{ padding: "8px" }}>Branch</th>
            <th style={{ padding: "8px" }}>Credential</th>
            <th style={{ padding: "8px" }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {!selectedAgentId && (
            <tr><td colSpan={5} style={{ padding: "12px", color: "#888" }}>Pick an agent above.</td></tr>
          )}
          {selectedAgentId && repos.length === 0 && (
            <tr><td colSpan={5} style={{ padding: "12px", color: "#888" }}>No repos for this agent.</td></tr>
          )}
          {repos.map((r) => {
            const cred = creds.find((c) => c.id === r.credential_id);
            return (
              <tr key={r.id} style={{ borderTop: "1px solid #eee" }}>
                <td style={{ padding: "8px" }}>{r.name}</td>
                <td style={{ padding: "8px" }}><code style={{ fontSize: "12px" }}>{r.url}</code></td>
                <td style={{ padding: "8px" }}>
                  {r.active_branch ? (
                    <><strong>{r.active_branch}</strong> <span style={{ color: "#888" }}>(default: {r.default_branch})</span></>
                  ) : (
                    r.default_branch
                  )}
                </td>
                <td style={{ padding: "8px" }}>{cred ? cred.name : <span style={{ color: "#888" }}>auto</span>}</td>
                <td style={{ padding: "8px" }}>
                  <button onClick={() => startEdit(r)}>Edit</button>{" "}
                  {confirmDelete === r.id ? (
                    <>
                      <button onClick={() => remove(r.id)} style={{ color: "#b91c1c" }}>Confirm delete</button>{" "}
                      <button onClick={() => setConfirmDelete(null)}>Cancel</button>
                    </>
                  ) : (
                    <button onClick={() => setConfirmDelete(r.id)}>Delete</button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
