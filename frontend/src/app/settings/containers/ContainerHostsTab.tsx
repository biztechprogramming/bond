"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
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
  daemon_installed: boolean;
}

const STRATEGIES = ["least-loaded", "round-robin", "manual"];

export default function ContainerHostsTab() {
  const [settings, setSettings] = useState<Record<string, string>>({});
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [settingsMsg, setSettingsMsg] = useState("");

  const [hosts, setHosts] = useState<ContainerHost[]>([]);
  const [showAdd, setShowAdd] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, string>>({});
  const [editingHost, setEditingHost] = useState<ContainerHost | null>(null);
  const [installingId, setInstallingId] = useState<string | null>(null);
  const [installResults, setInstallResults] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [installLog, setInstallLog] = useState<{ hostId: string; lines: { type?: string; step: string; status: string; message: string }[]; done: boolean; success: boolean } | null>(null);
  const installLogRef = React.useRef<HTMLDivElement>(null);
  const installSectionRef = useRef<HTMLElement>(null);
  // Auto-scroll the log div itself (not the page) on every log update
  useEffect(() => {
    if (installLog && installLogRef.current) {
      requestAnimationFrame(() => {
        installLogRef.current!.scrollTop = installLogRef.current!.scrollHeight;
      });
    }
  }, [installLog]);

  // When install log first appears, scroll the log div into view once
  const [logScrolledIntoView, setLogScrolledIntoView] = useState(false);
  useEffect(() => {
    if (installLog && !logScrolledIntoView && installLogRef.current) {
      // Short delay to let the log div render before scrolling
      setTimeout(() => {
        installLogRef.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }, 100);
      setLogScrolledIntoView(true);
    }
    if (!installLog) setLogScrolledIntoView(false);
  }, [installLog, logScrolledIntoView]);

  const fetchHosts = useCallback(async () => {
    try {
      const res = await apiFetch(API);
      if (res.ok) setHosts(await res.json());
    } catch { /* API not available */ }
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await apiFetch(`${API}/settings`);
      if (res.ok) setSettings(await res.json());
    } catch { /* API not available */ }
  }, []);

  useEffect(() => { fetchHosts(); fetchSettings(); }, [fetchHosts, fetchSettings]);

  const saveSetting = (key: string, value: string) => setSettings(prev => ({ ...prev, [key]: value }));

  const handleSaveSettings = async () => {
    setSettingsSaving(true);
    setSettingsMsg("");
    try {
      const res = await apiFetch(`${API}/settings`, {
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
    const res = await apiFetch(`${API}/${id}`, { method: "DELETE" });
    if (res.ok) fetchHosts();
  };

  const handleTestHost = async (id: string) => {
    setTestingId(id);
    try {
      const res = await apiFetch(`${API}/${id}/test`, { method: "POST" });
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
    setInstallLog({ hostId: id, lines: [], done: false, success: false });
    setInstallResults(prev => { const n = { ...prev }; delete n[id]; return n; });
    try {
      const res = await apiFetch(`${API}/${id}/install-daemon`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setInstallLog(prev => prev ? { ...prev, done: true, lines: [...prev.lines, { step: "error", status: "error", message: data.detail || "Installation failed" }] } : prev);
        setInstallingId(null);
        return;
      }
      const reader = res.body?.getReader();
      if (!reader) { setInstallingId(null); return; }
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const part of parts) {
          const line = part.replace(/^data: /, "").trim();
          if (!line) continue;
          try {
            const evt = JSON.parse(line);
            if (evt.type === "command" || evt.type === "info") {
              setInstallLog(prev => prev ? { ...prev, lines: [...prev.lines, { type: evt.type, step: "", status: "", message: evt.message }] } : prev);
            } else if (evt.status === "done") {
              setInstallLog(prev => prev ? { ...prev, done: true, success: !!evt.success, lines: [...prev.lines, { step: evt.step, status: evt.success ? "ok" : "error", message: evt.message }] } : prev);
              if (evt.success) fetchHosts();
            } else {
              setInstallLog(prev => prev ? { ...prev, lines: [...prev.lines, { step: evt.step, status: evt.status, message: evt.message }] } : prev);
            }
            // Auto-scroll log pane to bottom
            requestAnimationFrame(() => {
              if (installLogRef.current) {
                installLogRef.current.scrollTop = installLogRef.current.scrollHeight;
              }
            });
          } catch { /* skip malformed */ }
        }
      }
    } catch (err: any) {
      setInstallLog(prev => prev ? { ...prev, done: true, lines: [...prev.lines, { step: "error", status: "error", message: err.message }] } : prev);
    }
    setInstallingId(null);
  };

  const statusColor = (s: string) => s === "active" ? "#6cffa0" : s === "draining" ? "#ffcc44" : "#ff6c8a";

  return (
    <>
      <style>{`
        .cht-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
        .cht-section { background-color: #12121a; border-radius: 12px; padding: 24px; border: 1px solid #1e1e2e; overflow: visible; flex-shrink: 0; }
        .cht-input, .cht-select {
          background-color: #1e1e2e; border: 1px solid #2a2a3e; border-radius: 8px;
          padding: 10px 12px; color: #e0e0e8; font-size: 0.95rem; outline: none;
          width: 100%; box-sizing: border-box;
        }
        .cht-table-header { display: flex; padding: 8px 12px; font-size: 0.8rem; color: #5a5a6e; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; border-bottom: 1px solid #1e1e2e; }
        .cht-table-row { display: flex; align-items: center; padding: 12px; border-bottom: 1px solid #1e1e2e; gap: 8px; }
        .cht-host-actions { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
        .cht-host-label { display: none; font-size: 0.75rem; color: #5a5a6e; font-weight: 600; text-transform: uppercase; margin-bottom: 2px; }
        .cht-save-row { display: flex; align-items: center; gap: 12px; margin-top: 16px; }
        .cht-hosts-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; flex-wrap: wrap; gap: 8px; }
        @media (max-width: 768px) {
          .cht-grid { grid-template-columns: 1fr; }
          .cht-section { padding: 16px; }
          .cht-table-header { display: none !important; }
          .cht-table-row {
            flex-direction: column;
            align-items: flex-start;
            gap: 6px;
            padding: 16px 12px;
          }
          .cht-host-label { display: block; }
          .cht-host-actions { width: 100%; margin-top: 4px; }
          .cht-save-row { flex-direction: column; align-items: stretch; }
          .cht-install-log { max-height: none !important; }
        }
      `}</style>

      {/* Section A: Container Defaults */}
      <section className="cht-section">
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 20px 0" }}>Container Defaults</h2>

        <div className="cht-grid">
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.85rem", color: "#8888a0", fontWeight: 500 }}>Docker Image</label>
            <input className="cht-input" value={settings["container.default_image"] || ""} onChange={e => saveSetting("container.default_image", e.target.value)} placeholder="bond-worker:latest" />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.85rem", color: "#8888a0", fontWeight: 500 }}>Memory Limit (MB)</label>
            <input className="cht-input" type="number" value={settings["container.memory_limit_mb"] || ""} onChange={e => saveSetting("container.memory_limit_mb", e.target.value)} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.85rem", color: "#8888a0", fontWeight: 500 }}>CPU Limit</label>
            <input className="cht-input" type="number" step="0.5" value={settings["container.cpu_limit"] || ""} onChange={e => saveSetting("container.cpu_limit", e.target.value)} />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.85rem", color: "#8888a0", fontWeight: 500 }}>Placement Strategy</label>
            <select className="cht-select" value={settings["container.placement_strategy"] || "least-loaded"} onChange={e => saveSetting("container.placement_strategy", e.target.value)}>
              {STRATEGIES.map(st => <option key={st} value={st}>{st}</option>)}
            </select>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.85rem", color: "#8888a0", fontWeight: 500 }}>Network Mode</label>
            <input className="cht-input" value={settings["container.network_mode"] || ""} onChange={e => saveSetting("container.network_mode", e.target.value)} placeholder="bridge" />
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <label style={{ fontSize: "0.85rem", color: "#8888a0", fontWeight: 500 }}>Extra Labels</label>
            <input className="cht-input" value={settings["container.extra_labels"] || ""} onChange={e => saveSetting("container.extra_labels", e.target.value)} placeholder="env=dev,team=core" />
          </div>
        </div>

        <div className="cht-save-row">
          <button
            style={{ backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: 8, padding: "10px 20px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer", opacity: settingsSaving ? 0.6 : 1 }}
            onClick={handleSaveSettings}
            disabled={settingsSaving}
          >
            {settingsSaving ? "Saving..." : "Save Defaults"}
          </button>
          {settingsMsg && <span style={{ fontSize: "0.85rem", color: "#6cffa0" }}>{settingsMsg}</span>}
        </div>
      </section>

      {/* Section B: Hosts */}
      <section className="cht-section" style={{ marginTop: 24 }}>
        <div className="cht-hosts-header">
          <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>Container Hosts</h2>
          <button
            style={{ backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: 8, padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" }}
            onClick={() => setShowAdd(true)}
          >
            + Add Host
          </button>
        </div>

        <div style={{ display: "flex", flexDirection: "column" }}>
          {/* Desktop table header */}
          <div className="cht-table-header">
            <span style={{ flex: 2 }}>Name</span>
            <span style={{ flex: 2 }}>Host</span>
            <span style={{ flex: 1 }}>Status</span>
            <span style={{ flex: 1 }}>Agents</span>
            <span style={{ flex: 3 }}>Actions</span>
          </div>

          {hosts.map(h => (
            <div key={h.id} className="cht-table-row">
              <span style={{ flex: 2, color: "#e0e0e8", fontWeight: 500 }}>
                <span className="cht-host-label">Name</span>
                {h.name}
                {h.is_local && <span style={{ color: "#6c8aff", fontSize: "0.75rem", marginLeft: 6 }}>LOCAL</span>}
              </span>
              <span style={{ flex: 2, color: "#8888a0", fontFamily: "monospace", fontSize: "0.85rem" }}>
                <span className="cht-host-label">Host</span>
                {h.user || "bond"}@{h.host}{h.port > 0 ? `:${h.port}` : ""}
              </span>
              <span style={{ flex: 1 }}>
                <span className="cht-host-label">Status</span>
                <span style={{ color: statusColor(h.status), fontSize: "0.85rem", fontWeight: 500 }}>{h.status}</span>
                {!h.is_local && h.daemon_installed && <span style={{ color: "#6cffa0", fontSize: "0.7rem", marginLeft: 4 }}>daemon ✓</span>}
              </span>
              <span style={{ flex: 1, color: "#8888a0", fontSize: "0.85rem" }}>
                <span className="cht-host-label">Agents</span>
                {h.running_count}/{h.max_agents}
              </span>
              <span style={{ flex: 3 }} className="cht-host-actions">
                <button style={smallBtn} onClick={() => setEditingHost(h)}>Edit</button>
                {!h.is_local && (
                  <>
                    <button
                      style={{ ...smallBtn, opacity: testingId === h.id ? 0.6 : 1 }}
                      onClick={() => handleTestHost(h.id)}
                      disabled={testingId === h.id}
                    >
                      {testingId === h.id ? "..." : "Test"}
                    </button>
                    {!h.daemon_installed && (
                      <button
                        style={{ ...smallBtn, opacity: installingId === h.id ? 0.6 : 1 }}
                        onClick={() => handleInstallDaemon(h.id)}
                        disabled={installingId === h.id}
                      >
                        {installingId === h.id ? "Installing..." : "Install Daemon"}
                      </button>
                    )}
                    <button style={{ ...smallBtn, color: "#ff6c8a" }} onClick={() => handleDeleteHost(h.id)}>
                      Delete
                    </button>
                  </>
                )}
                {testResults[h.id] && (
                  <span style={{ fontSize: "0.75rem", color: testResults[h.id] === "Connected" ? "#6cffa0" : "#ff6c8a" }}>
                    {testResults[h.id]}
                  </span>
                )}
                {installResults[h.id] && (
                  <span style={{ fontSize: "0.75rem", color: installResults[h.id].ok ? "#6cffa0" : "#ff6c8a" }}>
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

      {installLog && (
        <section ref={installSectionRef} className="cht-section" style={{ marginTop: 24, marginBottom: 24 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
              Install Daemon — {installLog.hostId}
              {installLog.done && (
                <span style={{ marginLeft: 12, fontSize: "0.85rem", fontWeight: 500, color: installLog.success ? "#6cffa0" : "#ff6c8a" }}>
                  {installLog.success ? "SUCCESS" : "FAILED"}
                </span>
              )}
            </h2>
            {installLog.done && (
              <button
                style={{ ...smallBtn, color: "#8888a0" }}
                onClick={() => setInstallLog(null)}
              >
                Dismiss
              </button>
            )}
          </div>
          <div
            ref={installLogRef}
            className="cht-install-log"
            style={{
              backgroundColor: "#0a0a12",
              border: "1px solid #1e1e2e",
              borderRadius: 8,
              padding: 16,
              fontFamily: "monospace",
              fontSize: "0.82rem",
              lineHeight: 1.7,
              minHeight: 120,
              maxHeight: "min(400px, 50vh)",
              overflowY: "auto",
              color: "#b0b0c0",
              userSelect: "text",
              cursor: "text",
            }}
          >
            {installLog.lines.map((l, i) => {
              if (l.type === "info") {
                return (
                  <div key={i} style={{ color: "#6c8aff" }}>
                    <span style={{ marginRight: 8 }}>→</span>{l.message}
                  </div>
                );
              }
              if (l.type === "command") {
                return (
                  <div key={i} style={{ color: "#8888a0" }}>
                    <span style={{ marginRight: 8 }}>$</span>{l.message}
                  </div>
                );
              }
              return (
                <div key={i} style={{ color: l.status === "error" ? "#ff6c8a" : l.status === "ok" ? "#6cffa0" : "#b0b0c0" }}>
                  <span style={{ color: "#5a5a6e", marginRight: 8 }}>{l.status === "running" ? "⟳" : l.status === "ok" ? "✓" : "✗"}</span>
                  {l.message}
                </div>
              );
            })}
            {!installLog.done && (
              <div style={{ color: "#5a5a6e" }}>
                <span style={{ animation: "pulse 1s infinite" }}>▍</span>
              </div>
            )}
          </div>
        </section>
      )}
    </>
  );
}

const smallBtn: React.CSSProperties = {
  background: "none", border: "1px solid #2a2a3e", borderRadius: 6,
  padding: "4px 10px", color: "#8888a0", cursor: "pointer", fontSize: "0.8rem",
};
