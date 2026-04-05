"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";

const API = `${BACKEND_API}/test-spacetimedb`;

interface Status {
  running: boolean;
  port: number;
  host: string;
  container_id: string | null;
  uptime: string | null;
}

interface ConnectivityResult {
  reachable: boolean;
  latency_ms: number;
  error: string | null;
}

interface LogLine {
  type?: string;
  message: string;
}

export default function TestSpacetimeDB() {
  const [host, setHost] = useState("localhost");
  const [port, setPort] = useState(18797);
  const [module, setModule] = useState("bond-core-v2");
  const [settingsMsg, setSettingsMsg] = useState("");

  const [status, setStatus] = useState<Status | null>(null);
  const [hostConn, setHostConn] = useState<ConnectivityResult | null>(null);
  const [containerConn, setContainerConn] = useState<ConnectivityResult | null>(null);
  const [testingHost, setTestingHost] = useState(false);
  const [testingContainer, setTestingContainer] = useState(false);

  const [logs, setLogs] = useState<LogLine[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [stopping, setStopping] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);

  const scrollToBottom = () =>
    requestAnimationFrame(() => logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" }));

  useEffect(() => { scrollToBottom(); }, [logs]);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API}/status`);
      if (res.ok) setStatus(await res.json());
    } catch { /* ignore */ }
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch(`${API}/settings`);
      if (res.ok) {
        const data = await res.json();
        setHost(data.host);
        setPort(data.port);
        setModule(data.module);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchStatus(); fetchSettings(); }, [fetchStatus, fetchSettings]);

  // Poll status every 10s
  useEffect(() => {
    const id = setInterval(fetchStatus, 10000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const handleSaveSettings = async () => {
    setSettingsMsg("");
    try {
      const res = await fetch(`${API}/settings`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host, port, module }),
      });
      if (res.ok) setSettingsMsg("Saved");
      else setSettingsMsg("Failed to save");
    } catch { setSettingsMsg("Failed to save"); }
    setTimeout(() => setSettingsMsg(""), 3000);
  };

  const handleStart = async () => {
    setStreaming(true);
    setLogs([]);
    try {
      const res = await fetch(`${API}/start`, { method: "POST" });
      const reader = res.body?.getReader();
      if (!reader) { setStreaming(false); return; }
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
            setLogs(prev => [...prev, { type: evt.type || evt.status, message: evt.message }]);
          } catch { /* skip */ }
        }
      }
    } catch (err: any) {
      setLogs(prev => [...prev, { type: "error", message: err.message }]);
    }
    setStreaming(false);
    fetchStatus();
  };

  const handleStop = async () => {
    if (!confirm("Stop the test SpacetimeDB instance?")) return;
    setStopping(true);
    try {
      const res = await fetch(`${API}/stop`, { method: "POST" });
      const data = await res.json();
      setLogs(prev => [...prev, { type: "info", message: data.output || (data.success ? "Stopped." : "Failed to stop.") }]);
    } catch (err: any) {
      setLogs(prev => [...prev, { type: "error", message: err.message }]);
    }
    setStopping(false);
    fetchStatus();
  };

  const handleTestHost = async () => {
    setTestingHost(true);
    setHostConn(null);
    try {
      const res = await fetch(`${API}/test-connectivity`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host, port }),
      });
      if (res.ok) setHostConn(await res.json());
    } catch (err: any) {
      setHostConn({ reachable: false, latency_ms: 0, error: err.message });
    }
    setTestingHost(false);
  };

  const handleTestContainer = async () => {
    setTestingContainer(true);
    setContainerConn(null);
    try {
      const res = await fetch(`${API}/test-from-container`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ host, port }),
      });
      if (res.ok) setContainerConn(await res.json());
    } catch (err: any) {
      setContainerConn({ reachable: false, latency_ms: 0, error: err.message });
    }
    setTestingContainer(false);
  };

  const running = status?.running ?? false;

  return (
    <section style={{ backgroundColor: "#12121a", borderRadius: "12px", padding: "24px", border: "1px solid #1e1e2e" }}>
      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" }}>
        <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
          &#128451; Test SpacetimeDB
        </h2>
        <span style={{
          display: "inline-flex", alignItems: "center", gap: "6px",
          fontSize: "0.8rem", fontWeight: 600,
          color: running ? "#6cffa0" : "#ff6c8a",
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%",
            backgroundColor: running ? "#6cffa0" : "#ff6c8a",
            display: "inline-block",
          }} />
          {running ? "Running" : "Stopped"}
        </span>
      </div>

      {/* Settings */}
      <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr 2fr", gap: "12px", marginBottom: "12px" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <label style={{ fontSize: "0.8rem", color: "#8888a0", fontWeight: 500 }}>Host</label>
          <input
            style={inputStyle}
            value={host}
            onChange={e => setHost(e.target.value)}
            placeholder="localhost"
          />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <label style={{ fontSize: "0.8rem", color: "#8888a0", fontWeight: 500 }}>Port</label>
          <input
            style={inputStyle}
            type="number"
            value={port}
            onChange={e => setPort(Number(e.target.value))}
          />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
          <label style={{ fontSize: "0.8rem", color: "#8888a0", fontWeight: 500 }}>Module</label>
          <input
            style={inputStyle}
            value={module}
            onChange={e => setModule(e.target.value)}
            placeholder="bond-core-v2"
          />
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "16px" }}>
        <button style={btnSecondary} onClick={handleSaveSettings}>&#128190; Save Settings</button>
        {settingsMsg && <span style={{ fontSize: "0.8rem", color: "#6cffa0" }}>{settingsMsg}</span>}
      </div>

      {/* Status panel */}
      <div style={{
        backgroundColor: "#1a1a2e", borderRadius: "8px", padding: "12px 16px",
        marginBottom: "16px", fontSize: "0.85rem", color: "#c0c0d0",
        border: "1px solid #2a2a3e",
      }}>
        <div>
          <strong>Status:</strong>{" "}
          {status ? (
            running
              ? `Running (container ${status.container_id?.slice(0, 12)}, ${status.uptime})`
              : "Stopped"
          ) : "Loading..."}
        </div>
        <div>
          <strong>Host connectivity:</strong>{" "}
          {hostConn === null
            ? "Not tested"
            : hostConn.reachable
              ? <span style={{ color: "#6cffa0" }}>Reachable ({hostConn.latency_ms}ms)</span>
              : <span style={{ color: "#ff6c8a" }}>Unreachable — {hostConn.error}</span>}
        </div>
        <div>
          <strong>Container connectivity:</strong>{" "}
          {containerConn === null
            ? "Not tested"
            : containerConn.reachable
              ? <span style={{ color: "#6cffa0" }}>Reachable ({containerConn.latency_ms}ms)</span>
              : <span style={{ color: "#ff6c8a" }}>Unreachable — {containerConn.error}</span>}
        </div>
      </div>

      {/* Action buttons */}
      <div style={{ display: "flex", gap: "8px", marginBottom: "16px", flexWrap: "wrap" }}>
        <button
          style={{ ...btnPrimary, opacity: streaming ? 0.6 : 1 }}
          onClick={handleStart}
          disabled={streaming}
        >
          {streaming ? "Starting..." : "▶ Start"}
        </button>
        <button
          style={{ ...btnDanger, opacity: stopping || !running ? 0.6 : 1 }}
          onClick={handleStop}
          disabled={stopping || !running}
        >
          {stopping ? "Stopping..." : "⏹ Stop"}
        </button>
        <button
          style={{ ...btnSecondary, opacity: testingHost ? 0.6 : 1 }}
          onClick={handleTestHost}
          disabled={testingHost}
        >
          {testingHost ? "Testing..." : "🔍 Test Host"}
        </button>
        <button
          style={{ ...btnSecondary, opacity: testingContainer ? 0.6 : 1 }}
          onClick={handleTestContainer}
          disabled={testingContainer}
        >
          {testingContainer ? "Testing..." : "📦 Test Container"}
        </button>
      </div>

      {/* Log panel */}
      {logs.length > 0 && (
        <div>
          <div style={{ fontSize: "0.8rem", color: "#5a5a6e", fontWeight: 600, marginBottom: "6px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
            Log Output
          </div>
          <div
            ref={logRef}
            style={{
              backgroundColor: "#0d0d14",
              borderRadius: "8px",
              padding: "12px",
              maxHeight: "300px",
              overflowY: "auto",
              fontFamily: "monospace",
              fontSize: "0.8rem",
              lineHeight: "1.6",
              color: "#c0c0d0",
              border: "1px solid #1e1e2e",
            }}
          >
            {logs.map((l, i) => (
              <div key={i} style={{ color: l.type === "error" || l.type === "done" && l.message.includes("failed") ? "#ff6c8a" : l.type === "done" ? "#6cffa0" : "#c0c0d0" }}>
                {l.message}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

const inputStyle: React.CSSProperties = {
  backgroundColor: "#1e1e2e",
  border: "1px solid #2a2a3e",
  borderRadius: "8px",
  padding: "8px 12px",
  color: "#e0e0e8",
  fontSize: "0.9rem",
  outline: "none",
  width: "100%",
  boxSizing: "border-box",
};

const btnSecondary: React.CSSProperties = {
  backgroundColor: "#2a2a3e",
  color: "#e0e0e8",
  border: "1px solid #3a3a4e",
  borderRadius: "8px",
  padding: "8px 16px",
  fontSize: "0.85rem",
  cursor: "pointer",
};

const btnPrimary: React.CSSProperties = {
  backgroundColor: "#6c8aff",
  color: "#fff",
  border: "none",
  borderRadius: "8px",
  padding: "8px 16px",
  fontSize: "0.85rem",
  fontWeight: 600,
  cursor: "pointer",
};

const btnDanger: React.CSSProperties = {
  backgroundColor: "#4a1a2e",
  color: "#ff6c8a",
  border: "1px solid #ff6c8a44",
  borderRadius: "8px",
  padding: "8px 16px",
  fontSize: "0.85rem",
  cursor: "pointer",
};
