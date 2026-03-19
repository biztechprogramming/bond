"use client";

import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

const GATEWAY = GATEWAY_API;

interface SolidTimeStatus {
  configured: boolean;
  enabled?: boolean;
  organizationName?: string;
  userName?: string;
}

export default function SolidTimeCard() {
  const [status, setStatus] = useState<SolidTimeStatus | null>(null);
  const [loading, setLoading] = useState(true);

  // Setup wizard
  const [showSetup, setShowSetup] = useState(false);
  const [url, setUrl] = useState("http://localhost:8734");
  const [apiToken, setApiToken] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [error, setError] = useState("");

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${GATEWAY}/integrations/solidtime/status`);
      if (res.ok) setStatus(await res.json());
    } catch {
      /* gateway unavailable */
    }
    setLoading(false);
  };

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 10000);
    return () => clearInterval(interval);
  }, []);

  const connect = async () => {
    setConnecting(true);
    setError("");
    try {
      const res = await fetch(`${GATEWAY}/integrations/solidtime/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url, apiToken }),
      });
      const data = await res.json();
      if (!res.ok) {
        setError(data.error || "Connection failed");
        return;
      }
      setShowSetup(false);
      await fetchStatus();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed");
    } finally {
      setConnecting(false);
    }
  };

  const disconnect = async () => {
    await fetch(`${GATEWAY}/integrations/solidtime`, { method: "DELETE" });
    setShowSetup(false);
    setApiToken("");
    await fetchStatus();
  };

  if (loading) return null;

  return (
    <div style={s.card}>
      <div style={s.cardHeader}>
        <span style={s.channelName}>SolidTime</span>
        {status?.configured ? (
          <span style={{ ...s.badge, backgroundColor: "#1a3a1a", color: "#6cffa0" }}>Connected</span>
        ) : (
          <span style={{ ...s.badge, backgroundColor: "#2a2a3e", color: "#8888a0" }}>Not configured</span>
        )}
      </div>
      <p style={s.desc}>Time tracking integration — agents can log time, manage projects and tasks</p>

      {status?.configured && (
        <div style={s.linkedInfo}>
          <span>
            {status.organizationName && `Org: ${status.organizationName}`}
            {status.userName && ` — ${status.userName}`}
          </span>
          <button style={s.dangerBtn} onClick={disconnect}>Disconnect</button>
        </div>
      )}

      {!status?.configured && !showSetup && (
        <button style={s.setupBtn} onClick={() => setShowSetup(true)}>Set Up</button>
      )}

      {showSetup && !status?.configured && (
        <div style={s.wizard}>
          <ol style={s.steps}>
            <li>Open your SolidTime instance</li>
            <li>Go to User Settings &rarr; API Tokens</li>
            <li>Create a new API token and paste it below:</li>
          </ol>
          <input
            type="text"
            style={s.input}
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            placeholder="http://localhost:8734"
          />
          <input
            type="password"
            style={s.input}
            value={apiToken}
            onChange={(e) => setApiToken(e.target.value)}
            placeholder="API Token"
          />
          {error && <div style={s.error}>{error}</div>}
          <button
            style={{ ...s.setupBtn, opacity: connecting ? 0.5 : 1 }}
            onClick={connect}
            disabled={connecting || !apiToken.trim()}
          >
            {connecting ? "Connecting..." : "Connect"}
          </button>
        </div>
      )}
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  card: { backgroundColor: "#12121a", borderRadius: "12px", padding: "20px", border: "1px solid #1e1e2e" },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  channelName: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  badge: { fontSize: "0.75rem", padding: "4px 10px", borderRadius: "12px", fontWeight: 500 },
  desc: { color: "#8888a0", fontSize: "0.85rem", margin: "0 0 12px 0" },
  setupBtn: { backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" },
  dangerBtn: { backgroundColor: "#3a1a1a", color: "#ff6c8a", border: "1px solid #5a2a2a", borderRadius: "8px", padding: "6px 14px", fontSize: "0.8rem", fontWeight: 500, cursor: "pointer" },
  wizard: { marginTop: "12px", padding: "16px", backgroundColor: "#1e1e2e", borderRadius: "8px" },
  steps: { color: "#e0e0e8", fontSize: "0.85rem", margin: "0 0 12px 0", paddingLeft: "20px" },
  input: { width: "100%", backgroundColor: "#12121a", border: "1px solid #2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none", marginBottom: "12px", boxSizing: "border-box" as const },
  error: { color: "#ff6c8a", fontSize: "0.85rem", marginBottom: "8px" },
  linkedInfo: { display: "flex", justifyContent: "space-between", alignItems: "center", padding: "12px", backgroundColor: "#1e1e2e", borderRadius: "8px", color: "#e0e0e8", fontSize: "0.85rem" },
};
