"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";
import LoadingSkeleton from "./components/LoadingSkeleton";
import ErrorBanner from "./components/ErrorBanner";
import EmptyState from "./components/EmptyState";

const API = `${BACKEND_API}/optimization`;

interface RetentionData {
  observations: { total_count: number; oldest: string; newest: string; storage_estimate_mb: number };
  candidates: { total_count: number; promoted_count: number; storage_estimate_mb: number };
  retention_policy: {
    observations_max_days: number;
    observations_max_rows: number;
    auto_purge_enabled: boolean;
    last_purge_at: string;
    next_purge_at: string;
  };
}

export default function RetentionPanel() {
  const [data, setData] = useState<RetentionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [maxDays, setMaxDays] = useState(180);
  const [maxRows, setMaxRows] = useState(50000);
  const [autoPurge, setAutoPurge] = useState(true);
  const [saving, setSaving] = useState(false);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 3000); };

  const fetchRetention = useCallback(async () => {
    try {
      setError("");
      const res = await apiFetch(`${API}/retention`);
      if (!res.ok) throw new Error("Failed to load retention data");
      const d: RetentionData = await res.json();
      setData(d);
      setMaxDays(d.retention_policy.observations_max_days);
      setMaxRows(d.retention_policy.observations_max_rows);
      setAutoPurge(d.retention_policy.auto_purge_enabled);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchRetention(); }, [fetchRetention]);

  const save = async () => {
    setSaving(true);
    try {
      const res = await apiFetch(`${API}/retention`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ observations_max_days: maxDays, observations_max_rows: maxRows, auto_purge_enabled: autoPurge }),
      });
      if (!res.ok) throw new Error("Failed to save");
      showToast("Retention policy saved");
      fetchRetention();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <LoadingSkeleton lines={4} />;
  if (error) return <ErrorBanner message={error} onRetry={fetchRetention} />;
  if (!data) return <EmptyState message="No retention data available" icon="🗄" />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {toast && <div style={{ padding: "8px 12px", backgroundColor: "#1a1a2e", borderRadius: "8px", color: "#6cffa0", fontSize: "0.85rem" }}>{toast}</div>}

      {/* Storage stats */}
      <div style={cardStyle}>
        <h3 style={titleStyle}>Storage</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "12px" }}>
          <Stat label="Observations" value={data.observations.total_count.toLocaleString()} />
          <Stat label="Oldest" value={data.observations.oldest ? new Date(data.observations.oldest).toLocaleDateString() : "—"} />
          <Stat label="Newest" value={data.observations.newest ? new Date(data.observations.newest).toLocaleDateString() : "—"} />
          <Stat label="Storage" value={`${data.observations.storage_estimate_mb.toFixed(1)} MB`} />
        </div>
      </div>

      <div style={cardStyle}>
        <h3 style={titleStyle}>Candidates</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "12px" }}>
          <Stat label="Total" value={String(data.candidates.total_count)} />
          <Stat label="Promoted" value={String(data.candidates.promoted_count)} />
          <Stat label="Storage" value={`${data.candidates.storage_estimate_mb.toFixed(1)} MB`} />
        </div>
      </div>

      {/* Retention policy form */}
      <div style={cardStyle}>
        <h3 style={titleStyle}>Retention Policy</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <label style={labelStyle}>Max observation age (days)</label>
            <input type="number" value={maxDays} onChange={(e) => setMaxDays(Number(e.target.value))} min={7} max={365} style={inputStyle} />
          </div>
          <div>
            <label style={labelStyle}>Max observation rows</label>
            <input type="number" value={maxRows} onChange={(e) => setMaxRows(Number(e.target.value))} min={1000} max={500000} style={inputStyle} />
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
            <input type="checkbox" checked={autoPurge} onChange={(e) => setAutoPurge(e.target.checked)} style={{ accentColor: "#6c8aff", width: "16px", height: "16px" }} />
            <span style={{ color: "#e0e0e8", fontSize: "0.9rem" }}>Auto-purge enabled</span>
          </label>
          <div style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>
            Last purge: {data.retention_policy.last_purge_at ? new Date(data.retention_policy.last_purge_at).toLocaleString() : "Never"}
            {" · "}
            Next purge: {data.retention_policy.next_purge_at ? new Date(data.retention_policy.next_purge_at).toLocaleString() : "—"}
          </div>
          <button onClick={save} disabled={saving} style={{ ...btnStyle, opacity: saving ? 0.5 : 1, alignSelf: "flex-start" }}>
            {saving ? "Saving..." : "Save Policy"}
          </button>
        </div>
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ color: "#5a5a6e", fontSize: "0.75rem", textTransform: "uppercase", letterSpacing: "0.5px" }}>{label}</div>
      <div style={{ color: "#e0e0e8", fontSize: "1.1rem", fontWeight: 600 }}>{value}</div>
    </div>
  );
}

const cardStyle: React.CSSProperties = { backgroundColor: "#12121a", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", borderRadius: "12px", padding: "16px" };
const titleStyle: React.CSSProperties = { color: "#8888a0", fontSize: "0.85rem", fontWeight: 500, margin: "0 0 12px 0" };
const labelStyle: React.CSSProperties = { display: "block", fontSize: "0.85rem", color: "#8888a0", marginBottom: "4px" };
const inputStyle: React.CSSProperties = { backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none", width: "200px" };
const btnStyle: React.CSSProperties = { backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" };
