"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";
import ConfirmDialog from "./components/ConfirmDialog";
import LoadingSkeleton from "./components/LoadingSkeleton";
import ErrorBanner from "./components/ErrorBanner";
import EmptyState from "./components/EmptyState";

const API = `${BACKEND_API}/optimization`;

interface Experiment {
  id: string;
  status: string;
  proposed_value: number;
  control_mean_score: number;
  experiment_mean_score: number;
  p_value: number;
  conclusion: string;
  control_obs_count: number;
  experiment_obs_count: number;
}

interface Param {
  key: string;
  description: string;
  type: string;
  min: number;
  max: number;
  step: number;
  default_value: number;
  current_value: number;
  last_changed_at: string;
  last_changed_by: string;
  experiment?: Experiment;
}

interface HistoryEntry {
  value: number;
  changed_at: string;
  changed_by: string;
  experiment_id?: string;
}

export default function ParametersPanel() {
  const [params, setParams] = useState<Param[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [sliderValues, setSliderValues] = useState<Record<string, number>>({});
  const [confirm, setConfirm] = useState<{ key: string; action: "apply" | "experiment" | "rollback"; value?: number } | null>(null);
  const [historyKey, setHistoryKey] = useState<string | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 3000); };

  const fetchParams = useCallback(async () => {
    try {
      setError("");
      const res = await fetch(`${API}/params`);
      if (!res.ok) throw new Error("Failed to load parameters");
      const data = await res.json();
      setParams(data.params || []);
      const vals: Record<string, number> = {};
      for (const p of data.params || []) vals[p.key] = p.current_value;
      setSliderValues(vals);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchParams(); }, [fetchParams]);

  const applyParam = async (key: string, value: number) => {
    try {
      const res = await fetch(`${API}/params/${key}`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ value }) });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || "Failed"); }
      showToast("Parameter updated");
      fetchParams();
    } catch (e: unknown) { showToast(e instanceof Error ? e.message : "Failed"); }
  };

  const startExperiment = async (key: string, value: number) => {
    try {
      const res = await fetch(`${API}/experiments`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ param_key: key, proposed_value: value }) });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || "Failed"); }
      showToast("Experiment started");
      fetchParams();
    } catch (e: unknown) { showToast(e instanceof Error ? e.message : "Failed"); }
  };

  const rollback = async (key: string) => {
    try {
      const res = await fetch(`${API}/params/${key}/rollback`, { method: "POST" });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || "Failed"); }
      showToast("Rolled back");
      fetchParams();
    } catch (e: unknown) { showToast(e instanceof Error ? e.message : "Failed"); }
  };

  const loadHistory = async (key: string) => {
    if (historyKey === key) { setHistoryKey(null); return; }
    try {
      const res = await fetch(`${API}/params/${key}/history`);
      if (res.ok) { const data = await res.json(); setHistory(data.history || []); setHistoryKey(key); }
    } catch { /* ignore */ }
  };

  const handleConfirm = () => {
    if (!confirm) return;
    if (confirm.action === "apply" && confirm.value !== undefined) applyParam(confirm.key, confirm.value);
    else if (confirm.action === "experiment" && confirm.value !== undefined) startExperiment(confirm.key, confirm.value);
    else if (confirm.action === "rollback") rollback(confirm.key);
    setConfirm(null);
  };

  if (loading) return <LoadingSkeleton lines={5} />;
  if (error) return <ErrorBanner message={error} onRetry={fetchParams} />;
  if (params.length === 0) return <EmptyState message="No tunable parameters found" icon="🎛" />;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      {toast && <div style={{ padding: "8px 12px", backgroundColor: "#1a1a2e", borderRadius: "8px", color: "#6cffa0", fontSize: "0.85rem" }}>{toast}</div>}

      {params.map((p) => {
        const sliderVal = sliderValues[p.key] ?? p.current_value;
        const changed = sliderVal !== p.current_value;
        const exp = p.experiment;
        const promoted = exp && exp.status === "concluded" && exp.conclusion === "promoted";
        const scoreDiff = exp ? ((exp.experiment_mean_score - exp.control_mean_score) * 100).toFixed(0) : null;

        return (
          <div key={p.key} style={{ ...cardStyle, borderColor: changed ? "#ffcc44" : "#1e1e2e" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "4px" }}>
              <div>
                <strong style={{ color: "#e0e0e8", fontSize: "0.95rem" }}>{p.key}</strong>
                <span style={{ color: "#8888a0", fontSize: "0.85rem", marginLeft: "12px" }}>{sliderVal}</span>
              </div>
              <div style={{ display: "flex", gap: "6px" }}>
                <button style={btnSmallStyle} onClick={() => loadHistory(p.key)}>History</button>
                <button style={btnSmallStyle} onClick={() => setConfirm({ key: p.key, action: "rollback" })}>Rollback</button>
              </div>
            </div>
            <div style={{ color: "#5a5a6e", fontSize: "0.8rem", marginBottom: "8px" }}>{p.description}</div>
            <div style={{ color: "#5a5a6e", fontSize: "0.75rem", marginBottom: "8px" }}>
              Range: {p.min}–{p.max} · Step: {p.step} · Default: {p.default_value}
            </div>

            <input
              type="range"
              min={p.min}
              max={p.max}
              step={p.step}
              value={sliderVal}
              onChange={(e) => setSliderValues((prev) => ({ ...prev, [p.key]: Number(e.target.value) }))}
              style={{ width: "100%", accentColor: "#6c8aff" }}
              aria-label={`${p.key} value`}
            />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: "0.7rem", color: "#5a5a6e" }}>
              <span>{p.min}</span><span>{p.max}</span>
            </div>

            {/* Experiment recommendation */}
            {promoted && (
              <div style={{ marginTop: "8px", padding: "8px 12px", backgroundColor: "#1a2a1e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a4a2e", borderRadius: "8px", fontSize: "0.8rem", color: "#6cffa0" }}>
                Experiment suggests {exp.proposed_value} (p={exp.p_value.toFixed(2)}, +{scoreDiff}% score, n={exp.control_obs_count}/{exp.experiment_obs_count})
              </div>
            )}

            {/* Actions */}
            {changed && (
              <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                <button style={btnStyle} onClick={() => setConfirm({ key: p.key, action: "apply", value: sliderVal })}>Apply {sliderVal}</button>
                <button style={btnSecondaryStyle} onClick={() => setConfirm({ key: p.key, action: "experiment", value: sliderVal })}>Start Experiment</button>
              </div>
            )}

            {/* Inline history */}
            {historyKey === p.key && (
              <div style={{ marginTop: "10px", padding: "10px", backgroundColor: "#0a0a14", borderRadius: "8px" }}>
                {history.length === 0 ? <span style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>No history</span> : history.map((h, i) => (
                  <div key={i} style={{ display: "flex", gap: "12px", padding: "4px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", fontSize: "0.8rem" }}>
                    <span style={{ color: "#6c8aff" }}>{h.value}</span>
                    <span style={{ color: "#8888a0", flex: 1 }}>{h.changed_by}</span>
                    <span style={{ color: "#5a5a6e" }}>{new Date(h.changed_at).toLocaleDateString()}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        );
      })}

      <ConfirmDialog
        open={confirm?.action === "apply"}
        title="Apply parameter change?"
        message={confirm ? `Change ${confirm.key} to ${confirm.value}?` : ""}
        confirmLabel="Apply"
        onConfirm={handleConfirm}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.action === "experiment"}
        title="Start experiment?"
        message={confirm ? `Test ${confirm.key} = ${confirm.value} via A/B experiment?` : ""}
        confirmLabel="Start"
        onConfirm={handleConfirm}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.action === "rollback"}
        title="Rollback parameter?"
        message={confirm ? `Revert ${confirm.key} to its previous value?` : ""}
        confirmLabel="Rollback"
        onConfirm={handleConfirm}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}

const cardStyle: React.CSSProperties = { backgroundColor: "#12121a", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", borderRadius: "12px", padding: "16px" };
const btnStyle: React.CSSProperties = { backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" };
const btnSecondaryStyle: React.CSSProperties = { backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" };
const btnSmallStyle: React.CSSProperties = { background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "6px", padding: "4px 10px", color: "#8888a0", fontSize: "0.8rem", cursor: "pointer" };
