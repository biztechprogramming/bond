import React, { useState } from "react";
import { BACKEND_API } from "@/lib/config";

interface ContainerHost {
  id: string;
  name: string;
  host: string;
  port: number;
  user: string;
  daemon_port: number;
  max_agents: number;
  memory_mb: number;
  labels: string[];
}

interface EditContainerHostModalProps {
  host: ContainerHost;
  onComplete: () => void;
  onCancel: () => void;
}

export default function EditContainerHostModal({ host: h, onComplete, onCancel }: EditContainerHostModalProps) {
  const [name, setName] = useState(h.name);
  const [host, setHost] = useState(h.host);
  const [port, setPort] = useState(String(h.port));
  const [user, setUser] = useState(h.user);
  const [sshKey, setSshKey] = useState("");
  const [sshKeyChanged, setSshKeyChanged] = useState(false);
  const [daemonPort, setDaemonPort] = useState(String(h.daemon_port));
  const [maxAgents, setMaxAgents] = useState(String(h.max_agents));
  const [memoryMb, setMemoryMb] = useState(String(h.memory_mb));
  const [labels, setLabels] = useState(h.labels.join(", "));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    if (!host) { setError("Host is required."); return; }
    setSaving(true);
    setError("");
    try {
      const body: Record<string, unknown> = {
        name: name || h.id,
        host,
        port: parseInt(port),
        user,
        daemon_port: parseInt(daemonPort),
        max_agents: parseInt(maxAgents),
        memory_mb: parseInt(memoryMb),
        labels: labels ? labels.split(",").map(l => l.trim()).filter(Boolean) : [],
      };
      if (sshKeyChanged) body.ssh_key = sshKey;
      const res = await fetch(`${BACKEND_API}/hosts/${h.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      onComplete();
    } catch (err: any) {
      setError(err.message);
    }
    setSaving(false);
  };

  return (
    <div style={ms.overlay} onClick={onCancel}>
      <div style={ms.modal} onClick={e => e.stopPropagation()}>
        <div style={ms.header}>
          <h2 style={ms.title}>Edit Host: {h.id}</h2>
          <button style={ms.closeBtn} onClick={onCancel}>&times;</button>
        </div>

        <div style={ms.body}>
          <div style={ms.row}>
            <div style={ms.field}>
              <label style={ms.label}>Display Name</label>
              <input style={ms.input} value={name} onChange={e => setName(e.target.value)} />
            </div>
          </div>

          <div style={ms.row}>
            <div style={ms.field}>
              <label style={ms.label}>Host / IP</label>
              <input style={ms.input} value={host} onChange={e => setHost(e.target.value)} />
            </div>
            <div style={ms.field}>
              <label style={ms.label}>SSH Port</label>
              <input style={ms.input} type="number" value={port} onChange={e => setPort(e.target.value)} />
            </div>
          </div>

          <div style={ms.row}>
            <div style={ms.field}>
              <label style={ms.label}>SSH User</label>
              <input style={ms.input} value={user} onChange={e => setUser(e.target.value)} />
            </div>
            <div style={ms.field}>
              <label style={ms.label}>Daemon Port</label>
              <input style={ms.input} type="number" value={daemonPort} onChange={e => setDaemonPort(e.target.value)} />
            </div>
          </div>

          <div style={ms.field}>
            <label style={ms.label}>SSH Key {!sshKeyChanged && "(leave unchanged)"}</label>
            {!sshKeyChanged ? (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input style={{ ...ms.input, flex: 1 }} value="••••••••" disabled />
                <button style={ms.replaceBtn} onClick={() => setSshKeyChanged(true)}>Replace</button>
              </div>
            ) : (
              <textarea style={{ ...ms.input, minHeight: 80, fontFamily: "monospace", fontSize: "0.8rem" }} value={sshKey} onChange={e => setSshKey(e.target.value)} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            )}
          </div>

          <div style={ms.row}>
            <div style={ms.field}>
              <label style={ms.label}>Max Agents</label>
              <input style={ms.input} type="number" value={maxAgents} onChange={e => setMaxAgents(e.target.value)} />
            </div>
            <div style={ms.field}>
              <label style={ms.label}>Memory (MB, 0 = unlimited)</label>
              <input style={ms.input} type="number" value={memoryMb} onChange={e => setMemoryMb(e.target.value)} />
            </div>
          </div>

          <div style={ms.field}>
            <label style={ms.label}>Labels (comma-separated)</label>
            <input style={ms.input} value={labels} onChange={e => setLabels(e.target.value)} placeholder="gpu, high-memory" />
          </div>

          {error && <div style={ms.error}>{error}</div>}
        </div>

        <div style={ms.footer}>
          <button style={ms.cancelBtn} onClick={onCancel}>Cancel</button>
          <button style={{ ...ms.saveBtn, opacity: saving ? 0.6 : 1 }} onClick={handleSave} disabled={saving}>
            {saving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}

const ms: Record<string, React.CSSProperties> = {
  overlay: { position: "fixed", inset: 0, backgroundColor: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 },
  modal: { backgroundColor: "#12121a", borderRadius: 12, border: "1px solid #1e1e2e", width: "100%", maxWidth: 620, maxHeight: "90vh", display: "flex", flexDirection: "column", overflow: "hidden" },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "16px 24px", borderBottom: "1px solid #1e1e2e" },
  title: { margin: 0, fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8" },
  closeBtn: { background: "none", border: "none", color: "#8888a0", fontSize: "1.5rem", cursor: "pointer", padding: "0 4px" },
  body: { padding: "20px 24px", overflowY: "auto", flex: 1, display: "flex", flexDirection: "column", gap: 12 },
  row: { display: "flex", gap: 12 },
  field: { flex: 1, display: "flex", flexDirection: "column", gap: 4 },
  label: { fontSize: "0.8rem", color: "#8888a0", fontWeight: 500 },
  input: { backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: 8, padding: "8px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none", width: "100%", boxSizing: "border-box" },
  error: { padding: "8px 12px", borderRadius: 8, border: "1px solid #5a2a2e", color: "#ff6c8a", fontSize: "0.85rem" },
  footer: { display: "flex", justifyContent: "flex-end", gap: 8, padding: "16px 24px", borderTop: "1px solid #1e1e2e" },
  cancelBtn: { background: "none", border: "1px solid #2a2a3e", borderRadius: 8, padding: "8px 16px", color: "#8888a0", cursor: "pointer", fontSize: "0.9rem" },
  saveBtn: { backgroundColor: "#6c8aff", border: "none", borderRadius: 8, padding: "8px 16px", color: "#fff", cursor: "pointer", fontSize: "0.9rem", fontWeight: 600 },
  replaceBtn: { background: "none", border: "1px solid #2a2a3e", borderRadius: 6, padding: "6px 12px", color: "#8888a0", cursor: "pointer", fontSize: "0.8rem", whiteSpace: "nowrap" },
};
