"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";
import AddContainerHostModal from "./AddContainerHostModal";
import EditContainerHostModal from "./EditContainerHostModal";

const API = `${BACKEND_API}/hosts`;

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
  enabled: boolean;
  status: string;
  is_local: boolean;
  running_count: number;
}

const STRATEGIES = ["least-loaded", "round-robin", "manual"];

export default function ContainerHostsTab() {
  // Container settings
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsMsg, setSettingsMsg] = useState("");

  // Hosts
  const [hosts, setHosts] = useState<ContainerHost[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [editingHost, setEditingHost] = useState<ContainerHost | null>(null);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [installResults, setInstallResults] = useState<Record<string, { ok: boolean; msg: string }>>({});

  const fetchHosts = useCallback(async () => {
    try {
      const res = await fetch(API);
      if (res.ok) setHosts(await res.json());
    } catch { /* API not available */ }
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch(`${API}/settings`);
      if (res.ok) setSettings(await res.json());
    } catch { /* API not available */ }
  }, []);

  useEffect(() => { fetchHosts(); fetchSettings(); }, [fetchHosts, fetchSettings]);

  const saveSetting = (key: string, value: string) => setSettings(prev => ({ ...prev, [key]: value }));

  const handleSaveSettings = async () => {
    setSettingsSaving(true);
    setSettingsMsg("");
    try {
      const res = await fetch(`${API}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings }),
      });
      if (res.ok) {
        setSettings(await res.json());
        setSettingsMsg("Settings saved.");
      }
    } catch { setSettingsMsg("Failed to save."); }
    setSettingsSaving(false);
    setTimeout(() => setSettingsMsg(""), 3000);
  };

  const handleDeleteHost = async (id: string) => {
    if (!confirm(`Remove host "${id}"?`)) return;
    const res = await fetch(`${API}/${id}`, { method: "DELETE" });
    if (res.ok) fetchHosts();
  };

  const handleTestHost = async (id: string) => {
    setTestingId(id);
    try {
      const res = await fetch(`${API}/${id}/test`, { method: "POST" });
      const data = await res.json();
      const ok = data.ssh?.status === "ok";
      setTestResults(prev => ({ ...prev, [id]: ok ? "Connected" : (data.ssh?.error || "Failed") }));
    } catch (err: any) {
      setTestResults(prev => ({ ...prev, [id]: err.message }));
    }
    setTestingId(null);
  };

  const handleInstallDaemon = async (id: string) => {
    setInstallingId(id);
    setInstallResults(prev => { const next = { ...prev }; delete next[id]; return next; });
    try {
      const res = await fetch(`${API}/${id}/install-daemon`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        setInstallResults(prev => ({ ...prev, [id]: { ok: true, msg: "Daemon installed" } }));
        fetchHosts();
      } else {
        setInstallResults(prev => ({ ...prev, [id]: { ok: false, msg: data.detail || `HTTP ${res.status}` } }));
      }
    } catch (err: any) {
      setInstallResults(prev => ({ ...prev, [id]: { ok: false, msg: err.message } }));
    }
    setInstallingId(null);
  };

  const statusColor = (s: string) => s === "active" ? "#6cffa0" : s === "draining" ? "#ffcc44" : "#ff6c8a";

  return (
    <>
      {/* Section A: Container Defaults */}
      <section style={s.section}>
        <h2 style={s.sectionTitle}>Container Defaults</h2>

        <div style={s.grid}>
          <div style={s.field}>
            <label style={s.label}>Docker Image</label>
            <input style={s.input} value={settings["container.default_image"] || ""} onChange={e => saveSetting("container.default_image", e.target.value)} placeholder="bond-worker:latest" />
          </div>
          <div style={s.field}>
            <label style={s.label}>Memory Limit (MB)</label>
            <input style={s.input} type="number" value={settings["container.memory_limit_mb"] || ""} onChange={e => saveSetting("container.memory_limit_mb", e.target.value)} />
          </div>
          <div style={s.field}>
            <label style={s.label}>CPU Limit</label>
            <input style={s.input} type="number" step="0.5" value={settings["container.cpu_limit"] || ""} onChange={e => saveSetting("container.cpu_limit", e.target.value)} />
          </div>
          <div style={s.field}>
            <label style={s.label}>Placement Strategy</label>
            <select style={s.select} value={settings["container.placement_strategy"] || "least-loaded"} onChange={e => saveSetting("container.placement_strategy", e.target.value)}>
              {STRATEGIES.map(st => <option key={st} value={st}>{st}</option>)}
            </select>
          </div>
          <div style={s.field}>
            <label style={s.label}>Max Local Agents</label>
            <input style={s.input} type="number" value={settings["container.max_local_agents"] || ""} onChange={e => saveSetting("container.max_local_agents", e.target.value)} />
          </div>
          <div style={s.field}>
            <label style={s.label}>SSH Key Path</label>
            <input style={s.input} value={settings["container.ssh_key_path"] || ""} onChange={e => saveSetting("container.ssh_key_path", e.target.value)} />
          </div>
        </div>

        <div style={{ ...s.field, marginTop: 12 }}>
          <label style={{ ...s.label, display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
            <input type="checkbox" checked={settings["container.auto_pull_image"] === "true"} onChange={e => saveSetting("container.auto_pull_image", e.target.checked ? "true" : "false")} style={{ accentColor: "#6c8aff" }} />
            Auto-pull Docker image on container start
          </label>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 16 }}>
          <button style={{ ...s.button, opacity: settingsSaving ? 0.6 : 1 }} onClick={handleSaveSettings} disabled={settingsSaving}>
            {settingsSaving ? "Saving..." : "Save Settings"}
          </button>
          {settingsMsg && <span style={{ color: "#6cffa0", fontSize: "0.85rem" }}>{settingsMsg}</span>}
        </div>
      </section>

      {/* Section B: Container Hosts */}
      <section style={s.section}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <h2 style={{ ...s.sectionTitle, margin: 0 }}>Container Hosts</h2>
          <button style={s.button} onClick={() => setShowAdd(true)}>Add Host</button>
        </div>

        <div style={s.table}>
          <div style={s.tableHeader}>
            <span style={{ flex: 2 }}>Name</span>
            <span style={{ flex: 2 }}>Host</span>
            <span style={{ flex: 1 }}>Status</span>
            <span style={{ flex: 1 }}>Agents</span>
            <span style={{ flex: 1 }}>Memory</span>
            <span style={{ flex: 2, textAlign: "right" }}>Actions</span>
          </div>
          {hosts.map(h => (
            <div key={h.id} style={s.tableRow}>
              <span style={{ flex: 2, color: "#e0e0e8", fontWeight: 500 }}>
                {h.name}
                {h.is_local && <span style={{ color: "#6c8aff", fontSize: "0.75rem", marginLeft: 6 }}>LOCAL</span>}
              </span>
              <span style={{ flex: 2, color: "#8888a0", fontFamily: "monospace", fontSize: "0.85rem" }}>
                {h.host}{h.port > 0 ? `:${h.port}` : ""}
              </span>
              <span style={{ flex: 1 }}>
                <span style={{ color: statusColor(h.status), fontSize: "0.85rem", fontWeight: 500 }}>{h.status}</span>
              </span>
              <span style={{ flex: 1, color: "#8888a0", fontSize: "0.85rem" }}>
                {h.running_count}/{h.max_agents}
              </span>
              <span style={{ flex: 1, color: "#8888a0", fontSize: "0.85rem" }}>
                {h.memory_mb > 0 ? `${h.memory_mb} MB` : "—"}
              </span>
              <span style={{ flex: 2, display: "flex", justifyContent: "flex-end", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {!h.is_local && (
                  <>
                    <button style={s.smallBtn} onClick={() => setEditingHost(h)}>Edit</button>
                    <button
                      style={{ ...s.smallBtn, opacity: testingId === h.id ? 0.6 : 1 }}
                      onClick={() => handleTestHost(h.id)}
                      disabled={testingId === h.id}
                    >
                      {testingId === h.id ? "..." : "Test"}
                    </button>
                    <button
                      style={{ ...s.smallBtn, opacity: installingId === h.id ? 0.6 : 1 }}
                      onClick={() => handleInstallDaemon(h.id)}
                      disabled={installingId === h.id}
                    >
                      {installingId === h.id ? "Installing..." : "Install Daemon"}
                    </button>
                    <button style={{ ...s.smallBtn, color: "#ff6c8a" }} onClick={() => handleDeleteHost(h.id)}>
                      Delete
                    </button>
                  </>
                )}
                {testResults[h.id] && (
                  <span style={{ fontSize: "0.75rem", color: testResults[h.id] === "Connected" ? "#6cffa0" : "#ff6c8a", alignSelf: "center" }}>
                    {testResults[h.id]}
                  </span>
                )}
                {installResults[h.id] && (
                  <span style={{ fontSize: "0.75rem", color: installResults[h.id].ok ? "#6cffa0" : "#ff6c8a", alignSelf: "center" }}>
                    {installResults[h.id].msg}
                  </span>
                )}
              </span>
            </div>
          ))}
          {hosts.length === 0 && (
            <div style={{ padding: "24px 12px", color: "#5a5a6e", textAlign: "center", fontSize: "0.9rem" }}>
              No hosts configured. The local machine will be added automatically.
            </div>
          )}
        </div>
      </section>

      {showAdd && (
        <AddContainerHostModal
          onComplete={() => { setShowAdd(false); fetchHosts(); }}
          onCancel={() => setShowAdd(false)}
        />
      )}

      {editingHost && (
        <EditContainerHostModal
          host={editingHost}
          onComplete={() => { setEditingHost(null); fetchHosts(); }}
          onCancel={() => setEditingHost(null)}
        />
      )}
    </>
  );
}

const s: Record<string, React.CSSProperties> = {
  section: { backgroundColor: "#12121a", borderRadius: 12, padding: 24, border: "1px solid #1e1e2e" },
  sectionTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 20px 0" },
  grid: { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 },
  field: { display: "flex", flexDirection: "column", gap: 4 },
  label: { fontSize: "0.85rem", color: "#8888a0", fontWeight: 500 },
  input: { backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: 8, padding: "10px 12px", color: "#e0e0e8", fontSize: "0.95rem", outline: "none" },
  select: { backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: 8, padding: "10px 12px", color: "#e0e0e8", fontSize: "0.95rem", outline: "none" },
  button: { backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: 8, padding: "10px 20px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer" },
  table: { display: "flex", flexDirection: "column", gap: 0 },
  tableHeader: { display: "flex", padding: "8px 12px", fontSize: "0.8rem", color: "#5a5a6e", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", borderBottom: "1px solid #1e1e2e" },
  tableRow: { display: "flex", alignItems: "center", padding: "12px", borderBottom: "1px solid #1e1e2e" },
  smallBtn: { background: "none", border: "1px solid #2a2a3e", borderRadius: 6, padding: "4px 10px", color: "#8888a0", cursor: "pointer", fontSize: "0.8rem" },
};
