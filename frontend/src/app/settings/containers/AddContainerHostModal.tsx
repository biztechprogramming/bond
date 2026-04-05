import React, { useState } from "react";
import { BACKEND_API , apiFetch } from "@/lib/config";

interface AddContainerHostModalProps {
  onComplete: () => void;
  onCancel: () => void;
}

export default function AddContainerHostModal({ onComplete, onCancel }: AddContainerHostModalProps) {
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [user, setUser] = useState("bond");
  const [sshKey, setSshKey] = useState("");
  const [daemonPort, setDaemonPort] = useState("8990");
  const [maxAgents, setMaxAgents] = useState("4");
  const [memoryMb, setMemoryMb] = useState("0");
  const [labels, setLabels] = useState("");
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState("");
  const [error, setError] = useState("");

  const handleTest = async () => {
    setTesting(true);
    setTestResult("");
    try {
      const res = await apiFetch(`${BACKEND_API}/hosts/test-connection`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host, port: Number(port), user, ssh_key: sshKey || null }),
      });
      const data = await res.json();
      setTestResult(data.ssh?.status === "ok" ? "✓ Connected" : (data.ssh?.error || "Failed"));
    } catch (err: any) {
      setTestResult(err.message);
    }
    setTesting(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setError("");
    try {
      const res = await apiFetch(`${BACKEND_API}/hosts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          id, name, host, port: Number(port), user,
          ssh_key: sshKey || null,
          daemon_port: Number(daemonPort),
          max_agents: Number(maxAgents),
          memory_mb: Number(memoryMb),
          labels: labels ? labels.split(",").map(l => l.trim()) : [],
        }),
      });
      if (res.ok) { onComplete(); }
      else {
        const data = await res.json().catch(() => ({}));
        setError(data.detail || "Failed to add host");
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
        .cht-modal-hint { font-size: 0.75rem; color: #5a5a6e; margin-top: 2px; }
        @media (max-width: 768px) {
          .cht-modal-body { padding: 16px; }
          .cht-modal-header { padding: 16px; }
          .cht-modal-footer { padding: 12px 16px; }
          .cht-modal-row { grid-template-columns: 1fr; }
        }
      `}</style>
      <div className="cht-modal" onClick={e => e.stopPropagation()}>
        <div className="cht-modal-header">
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", margin: 0 }}>Add Container Host</h2>
          <button style={{ background: "none", border: "none", color: "#5a5a6e", fontSize: "1.5rem", cursor: "pointer" }} onClick={onCancel}>&times;</button>
        </div>

        <div className="cht-modal-body">
          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">ID (unique slug)</label>
              <input className="cht-modal-input" value={id} onChange={e => setId(e.target.value)} placeholder="gpu-server-1" />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">Display Name</label>
              <input className="cht-modal-input" value={name} onChange={e => setName(e.target.value)} placeholder="GPU Server 1" />
            </div>
          </div>

          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">Host</label>
              <input className="cht-modal-input" value={host} onChange={e => setHost(e.target.value)} placeholder="192.168.1.50" />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">SSH Port</label>
              <input className="cht-modal-input" type="number" value={port} onChange={e => setPort(e.target.value)} />
            </div>
          </div>

          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">SSH User</label>
              <input className="cht-modal-input" value={user} onChange={e => setUser(e.target.value)} />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">Daemon Port</label>
              <input className="cht-modal-input" type="number" value={daemonPort} onChange={e => setDaemonPort(e.target.value)} />
            </div>
          </div>

          <div className="cht-modal-row">
            <div className="cht-modal-field">
              <label className="cht-modal-label">Max Agents</label>
              <input className="cht-modal-input" type="number" value={maxAgents} onChange={e => setMaxAgents(e.target.value)} />
            </div>
            <div className="cht-modal-field">
              <label className="cht-modal-label">Memory (MB)</label>
              <input className="cht-modal-input" type="number" value={memoryMb} onChange={e => setMemoryMb(e.target.value)} placeholder="0 = auto-detect" />
            </div>
          </div>

          <div className="cht-modal-field">
            <label className="cht-modal-label">Labels (comma-separated)</label>
            <input className="cht-modal-input" value={labels} onChange={e => setLabels(e.target.value)} placeholder="gpu,high-mem" />
          </div>

          <div className="cht-modal-field">
            <label className="cht-modal-label">SSH Private Key (optional)</label>
            <textarea className="cht-modal-textarea" value={sshKey} onChange={e => setSshKey(e.target.value)} placeholder="Leave blank to use keys from ~/.ssh (id_rsa, id_ed25519, etc.)" />
            <span className="cht-modal-hint">If your SSH keys are already configured on this machine, you can leave this blank.</span>
          </div>

          {error && (
            <div style={{ padding: "8px 12px", borderRadius: 8, border: "1px solid #5a2a2e", color: "#ff6c8a", fontSize: "0.85rem" }}>
              {error}
            </div>
          )}
          {testResult && (
            <div style={{ fontSize: "0.85rem", color: testResult.startsWith("✓") ? "#6cffa0" : "#ff6c8a" }}>
              {testResult}
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
            style={{ backgroundColor: "#2a2a3e", border: "1px solid #3a3a5e", borderRadius: 8, padding: "8px 16px", color: "#e0e0e8", cursor: "pointer", fontSize: "0.9rem", fontWeight: 500, opacity: testing ? 0.6 : 1 }}
            onClick={handleTest}
            disabled={testing || !host}
          >
            {testing ? "Testing..." : "Test Connection"}
          </button>
          <button
            style={{ backgroundColor: "#6c8aff", border: "none", borderRadius: 8, padding: "8px 16px", color: "#fff", cursor: "pointer", fontSize: "0.9rem", fontWeight: 600, opacity: saving ? 0.6 : 1 }}
            onClick={handleSave}
            disabled={saving || !id || !host}
          >
            {saving ? "Saving..." : "Add Host"}
          </button>
        </div>
      </div>
    </div>
  );
}
