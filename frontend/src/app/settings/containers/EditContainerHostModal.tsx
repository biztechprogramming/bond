import React, { useState } from "react";
import { BACKEND_API , apiFetch } from "@/lib/config";

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
  const [labels, setLabels] = useState(h.labels?.join(", ") || "");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      const body: Record<string, any> = {
        name, host, port: Number(port), user,
        daemon_port: Number(daemonPort),
        max_agents: Number(maxAgents),
        memory_mb: Number(memoryMb),
        labels: labels ? labels.split(",").map(l => l.trim()) : [],
      };
      if (sshKeyChanged && sshKey) body.ssh_key = sshKey;
      const res = await apiFetch(`${BACKEND_API}/hosts/${h.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) { onComplete(); }
      else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Failed to update host");
      }
    } catch (err: any) { setError(err.message); }
    setSaving(false);
  };

  return (
    <div className="cht-modal-overlay" onClick={onCancel}>
      <style>{`
        .cht-modal-overlay {
          position: fixed; inset: 0; background: rgba(0,0,0,0.6);
          display: flex; align-items: center; justify-content: center;
          z-index: 1000; padding: 16px; box-sizing: border-box;
        }
        .cht-modal {
          background: #12121a; border: 1px solid #1e1e2e; border-radius: 12px;
          width: 100%; max-width: 560px; max-height: 90vh; overflow-y: auto;
          display: flex; flex-direction: column;
        }
        .cht-modal-header {
          display: flex; justify-content: space-between; align-items: center;
          padding: 20px 24px; border-bottom: 1px solid #1e1e2e;
        }
        .cht-modal-body { padding: 24px; display: flex; flex-direction: column; gap: 16px; }
        .cht-modal-row { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        .cht-modal-field { display: flex; flex-direction: column; gap: 4px; }
        .cht-modal-label { font-size: 0.85rem; color: #8888a0; font-weight: 500; }
        .cht-modal-input {
          background-color: #1e1e2e; border: 1px solid #2a2a3e; border-radius: 8px;
          padding: 8px 12px; color: #e0e0e8; font-size: 0.9rem; outline: none;
          width: 100%; box-sizing: border-box;
        }
        .cht-modal-textarea {
          background-color: #1e1e2e; border: 1px solid #2a2a3e; border-radius: 8px;
          padding: 8px 12px; color: #e0e0e8; font-size: 0.85rem; font-family: monospace;
          outline: none; width: 100%; box-sizing: border-box; resize: vertical; min-height: 80px;
        }
        .cht-modal-footer {
          display: flex; justify-content: flex-end; gap: 8px;
          padding: 16px 24px; border-top: 1px solid #1e1e2e; flex-wrap: wrap;
        }
        @media (max-width: 768px) {
          .cht-modal-body { padding: 16px; }
          .cht-modal-header { padding: 16px; }
          .cht-modal-footer { padding: 12px 16px; }
          .cht-modal-row { grid-template-columns: 1fr; }
        }
      `}</style>
      <div className="cht-modal" onClick={e => e.stopPropagation()}>
        <div className="cht-modal-header">
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", margin: 0 }}>Edit Host: {h.name}</h2>
          <button style={{ background: "none", border: "none", color: "#5a5a6e", fontSize: "1.5rem", cursor: "pointer" }} onClick={onCancel}>&times;</button>
        </div>

        <div className="cht-modal-body">
          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">Display Name</label>
              <input className="cht-modal-input" value={name} onChange={e => setName(e.target.value)} />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">Host</label>
              <input className="cht-modal-input" value={host} onChange={e => setHost(e.target.value)} />
            </div>
          </div>

          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">SSH Port</label>
              <input className="cht-modal-input" type="number" value={port} onChange={e => setPort(e.target.value)} />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">SSH User</label>
              <input className="cht-modal-input" value={user} onChange={e => setUser(e.target.value)} />
            </div>
          </div>

          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">Daemon Port</label>
              <input className="cht-modal-input" type="number" value={daemonPort} onChange={e => setDaemonPort(e.target.value)} />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">Max Agents</label>
              <input className="cht-modal-input" type="number" value={maxAgents} onChange={e => setMaxAgents(e.target.value)} />
            </div>
          </div>

          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">Memory (MB)</label>
              <input className="cht-modal-input" type="number" value={memoryMb} onChange={e => setMemoryMb(e.target.value)} />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">Labels (comma-separated)</label>
              <input className="cht-modal-input" value={labels} onChange={e => setLabels(e.target.value)} placeholder="gpu,high-mem" />
            </div>
          </div>

          <div className="cht-modal-field">
            <label className="cht-modal-label">SSH Private Key</label>
            {!sshKeyChanged ? (
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <span style={{ color: "#5a5a6e", fontSize: "0.9rem" }}>••••••••</span>
                <button
                  style={{ background: "none", border: "1px solid #2a2a3e", borderRadius: 6, padding: "6px 12px", color: "#8888a0", cursor: "pointer", fontSize: "0.8rem", whiteSpace: "nowrap" }}
                  onClick={() => setSshKeyChanged(true)}
                >
                  Replace
                </button>
              </div>
            ) : (
              <textarea className="cht-modal-textarea" value={sshKey} onChange={e => setSshKey(e.target.value)} placeholder="Paste new SSH private key..." />
            )}
          </div>

          {error && (
            <div style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid #5a2a2e", color: "#ff6c8a", fontSize: "0.85rem" }}>
              {error}
            </div>
          )}
        </div>

        <div className="cht-modal-footer">
          <button
            style={{ background: "none", border: "1px solid #2a2a3e", borderRadius: 8, padding: "8px 16px", color: "#8888a0", cursor: "pointer", fontSize: "0.9rem" }}
            onClick={onCancel}
          >
            Cancel
          </button>
          <button
            style={{ backgroundColor: "#6c8aff", border: "none", borderRadius: 8, padding: "8px 16px", color: "#fff", cursor: "pointer", fontSize: "0.9rem", fontWeight: 600, opacity: saving ? 0.6 : 1 }}
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? "Saving..." : "Save Changes"}
          </button>
        </div>
      </div>
    </div>
  );
}
