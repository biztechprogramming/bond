import React, { useState, useEffect, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";

/** Design Doc 113 — git credentials management. */

interface GitCredential {
  id: string;
  name: string;
  auth_type: "https_pat" | "ssh_key";
  host_pattern: string;
  username: string;
  is_default: boolean;
  secret_set: boolean;
  secret_hint: string;
  created_at: number;
}

interface CredForm {
  name: string;
  auth_type: "https_pat" | "ssh_key";
  host_pattern: string;
  username: string;
  is_default: boolean;
  secret: string;
}

const empty = (): CredForm => ({
  name: "",
  auth_type: "https_pat",
  host_pattern: "github.com",
  username: "",
  is_default: false,
  secret: "",
});

export default function GitCredentialsTab() {
  const [creds, setCreds] = useState<GitCredential[]>([]);
  const [editing, setEditing] = useState<CredForm | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const fetchCreds = useCallback(async () => {
    try {
      const res = await apiFetch(`${BACKEND_API}/git-credentials`);
      if (res.ok) setCreds(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchCreds();
  }, [fetchCreds]);

  const startCreate = () => {
    setEditing(empty());
    setEditingId(null);
    setIsNew(true);
    setMsg("");
  };

  const startEdit = (c: GitCredential) => {
    setEditing({
      name: c.name,
      auth_type: c.auth_type,
      host_pattern: c.host_pattern,
      username: c.username,
      is_default: c.is_default,
      secret: "",
    });
    setEditingId(c.id);
    setIsNew(false);
    setMsg("");
  };

  const cancel = () => {
    setEditing(null);
    setEditingId(null);
    setIsNew(false);
    setMsg("");
  };

  const save = async () => {
    if (!editing) return;
    if (!editing.name.trim() || !editing.host_pattern.trim()) {
      setMsg("Name and host pattern are required.");
      return;
    }
    if (isNew && !editing.secret.trim()) {
      setMsg("Secret is required for a new credential.");
      return;
    }

    const payload: Record<string, unknown> = {
      name: editing.name,
      auth_type: editing.auth_type,
      host_pattern: editing.host_pattern,
      username: editing.username,
      is_default: editing.is_default,
    };
    if (editing.secret) payload.secret = editing.secret;

    const url = isNew
      ? `${BACKEND_API}/git-credentials`
      : `${BACKEND_API}/git-credentials/${editingId}`;
    const method = isNew ? "POST" : "PUT";
    const res = await apiFetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (res.ok) {
      await fetchCreds();
      cancel();
    } else {
      const text = await res.text();
      setMsg(`Save failed: ${text}`);
    }
  };

  const remove = async (id: string) => {
    const res = await apiFetch(`${BACKEND_API}/git-credentials/${id}`, {
      method: "DELETE",
    });
    if (res.ok) {
      await fetchCreds();
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
          <h2 style={{ margin: 0 }}>Git Credentials</h2>
          <p style={{ margin: "4px 0 0", color: "#666", fontSize: "13px" }}>
            User-level git auth used to clone repos into agent containers. Resolved per repo via{" "}
            <code>host_pattern</code> match (e.g. <code>github.com</code>) or per-repo override.
          </p>
        </div>
        {!editing && (
          <button onClick={startCreate}>+ Add credential</button>
        )}
      </div>

      {msg && <div style={{ color: "#b91c1c", marginBottom: "8px" }}>{msg}</div>}

      {editing && (
        <div style={{ border: "1px solid #ccc", padding: "16px", marginBottom: "16px", borderRadius: "4px" }}>
          <h3 style={{ marginTop: 0 }}>{isNew ? "New credential" : "Edit credential"}</h3>
          <div style={{ display: "grid", gap: "10px", maxWidth: "560px" }}>
            <label>
              Name
              <input
                type="text"
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                placeholder="e.g. GitHub PAT"
                style={{ display: "block", width: "100%" }}
              />
            </label>
            <label>
              Auth type
              <select
                value={editing.auth_type}
                onChange={(e) => setEditing({ ...editing, auth_type: e.target.value as "https_pat" | "ssh_key" })}
                style={{ display: "block", width: "100%" }}
              >
                <option value="https_pat">HTTPS personal access token</option>
                <option value="ssh_key">SSH private key</option>
              </select>
            </label>
            <label>
              Host pattern
              <input
                type="text"
                value={editing.host_pattern}
                onChange={(e) => setEditing({ ...editing, host_pattern: e.target.value })}
                placeholder="github.com or dev.azure.com or *.gitlab.example.com or *"
                style={{ display: "block", width: "100%" }}
              />
            </label>
            {editing.auth_type === "https_pat" && (
              <label>
                Username
                <input
                  type="text"
                  value={editing.username}
                  onChange={(e) => setEditing({ ...editing, username: e.target.value })}
                  placeholder="Leave empty for x-access-token"
                  style={{ display: "block", width: "100%" }}
                />
                {editing.host_pattern.includes("azure.com") && (
                  <span style={{ fontSize: "11px", color: "#888", marginTop: "4px", display: "block" }}>
                    Azure DevOps: set this to your org name (e.g. <code>alliedim</code>) to match the username embedded in your clone URLs.
                    Generate a PAT at <strong>User settings → Personal access tokens</strong> with <em>Code (Read)</em> scope.
                  </span>
                )}
              </label>
            )}
            <label>
              {isNew
                ? (editing.auth_type === "ssh_key" ? "Private key" : "Personal access token")
                : "Replace secret (leave empty to keep existing)"}
              {editing.auth_type === "ssh_key" ? (
                <textarea
                  value={editing.secret}
                  onChange={(e) => setEditing({ ...editing, secret: e.target.value })}
                  placeholder={isNew ? "-----BEGIN OPENSSH PRIVATE KEY-----\n…\n-----END OPENSSH PRIVATE KEY-----" : "***"}
                  rows={8}
                  style={{ display: "block", width: "100%", fontFamily: "monospace", fontSize: "12px" }}
                />
              ) : (
                <input
                  type="password"
                  value={editing.secret}
                  onChange={(e) => setEditing({ ...editing, secret: e.target.value })}
                  placeholder={isNew ? "Personal access token" : "***"}
                  style={{ display: "block", width: "100%" }}
                />
              )}
            </label>
            <label>
              <input
                type="checkbox"
                checked={editing.is_default}
                onChange={(e) => setEditing({ ...editing, is_default: e.target.checked })}
              />
              {" "}Use as default when host pattern matches
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
            <th style={{ padding: "8px" }}>Type</th>
            <th style={{ padding: "8px" }}>Host pattern</th>
            <th style={{ padding: "8px" }}>Default?</th>
            <th style={{ padding: "8px" }}>Secret</th>
            <th style={{ padding: "8px" }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {creds.length === 0 && (
            <tr><td colSpan={6} style={{ padding: "12px", color: "#888" }}>No credentials yet.</td></tr>
          )}
          {creds.map((c) => (
            <tr key={c.id} style={{ borderTop: "1px solid #eee" }}>
              <td style={{ padding: "8px" }}>{c.name}</td>
              <td style={{ padding: "8px" }}>{c.auth_type === "https_pat" ? "HTTPS PAT" : "SSH key"}</td>
              <td style={{ padding: "8px" }}><code>{c.host_pattern}</code></td>
              <td style={{ padding: "8px" }}>{c.is_default ? "Yes" : ""}</td>
              <td style={{ padding: "8px" }}>
                {c.secret_set ? (
                  <span style={{ color: "#888" }}>•••• <code>{c.secret_hint}</code></span>
                ) : (
                  <span style={{ color: "#b91c1c" }}>missing</span>
                )}
              </td>
              <td style={{ padding: "8px" }}>
                <button onClick={() => startEdit(c)}>Edit</button>{" "}
                {confirmDelete === c.id ? (
                  <>
                    <button onClick={() => remove(c.id)} style={{ color: "#b91c1c" }}>Confirm delete</button>{" "}
                    <button onClick={() => setConfirmDelete(null)}>Cancel</button>
                  </>
                ) : (
                  <button onClick={() => setConfirmDelete(c.id)}>Delete</button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
