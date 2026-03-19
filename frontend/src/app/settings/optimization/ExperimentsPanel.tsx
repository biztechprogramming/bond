"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";
import ProgressBar from "./charts/ProgressBar";
import ConfirmDialog from "./components/ConfirmDialog";
import Pagination from "./components/Pagination";
import LoadingSkeleton from "./components/LoadingSkeleton";
import ErrorBanner from "./components/ErrorBanner";
import EmptyState from "./components/EmptyState";

const API = `${BACKEND_API}/optimization`;

interface Experiment {
  id: string;
  param_key: string;
  baseline_value: string;
  proposed_value: string;
  rationale: string;
  status: string;
  created_at: string;
  control_obs_count: number;
  experiment_obs_count: number;
  control_mean_score: number;
  experiment_mean_score: number;
  min_obs_per_cohort: number;
  max_duration_days: number;
  expires_at: string;
  estimated_completion: string;
  conclusion?: string;
  concluded_at?: string;
  p_value?: number;
  cancelled_at?: string;
  cancel_reason?: string;
}

interface ExpResponse {
  items: Experiment[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

type Status = "all" | "active" | "concluded" | "cancelled";

export default function ExperimentsPanel() {
  const [status, setStatus] = useState<Status>("all");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<ExpResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [confirm, setConfirm] = useState<{ id: string; action: "conclude" | "cancel" } | null>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 3000); };

  const fetchExperiments = useCallback(async () => {
    try {
      setError("");
      const params = new URLSearchParams({ status, page: String(page), per_page: "20" });
      const res = await fetch(`${API}/experiments?${params}`);
      if (!res.ok) throw new Error("Failed to load experiments");
      setData(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [status, page]);

  useEffect(() => { setLoading(true); setPage(1); }, [status]);
  useEffect(() => { fetchExperiments(); }, [fetchExperiments]);

  const doAction = async (id: string, action: string, body?: Record<string, unknown>) => {
    try {
      const res = await fetch(`${API}/experiments/${id}/${action}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || "Failed"); }
      showToast(action === "conclude" ? "Experiment concluded" : "Experiment cancelled");
      fetchExperiments();
    } catch (e: unknown) { showToast(e instanceof Error ? e.message : "Failed"); }
  };

  const handleConfirm = (textareaValue?: string) => {
    if (!confirm) return;
    if (confirm.action === "cancel") doAction(confirm.id, "cancel", textareaValue ? { reason: textareaValue } : undefined);
    else doAction(confirm.id, "conclude");
    setConfirm(null);
  };

  if (loading) return <LoadingSkeleton lines={4} />;
  if (error) return <ErrorBanner message={error} onRetry={fetchExperiments} />;

  const items = data?.items || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      {toast && <div style={{ padding: "8px 12px", backgroundColor: "#1a1a2e", borderRadius: "8px", color: "#6cffa0", fontSize: "0.85rem" }}>{toast}</div>}

      <div style={{ display: "flex", gap: "4px" }}>
        {(["all", "active", "concluded", "cancelled"] as Status[]).map((s) => (
          <button key={s} onClick={() => setStatus(s)} style={{
            background: status === s ? "#6c8aff" : "none",
            color: status === s ? "#fff" : "#8888a0",
            border: status === s ? "none" : "1px solid #2a2a3e",
            borderRadius: "6px", padding: "6px 14px", fontSize: "0.8rem", cursor: "pointer", textTransform: "capitalize",
          }}>{s}</button>
        ))}
      </div>

      {items.length === 0 ? (
        <EmptyState message="No experiments found" icon="🧪" />
      ) : (
        items.map((exp) => {
          const progress = exp.min_obs_per_cohort > 0
            ? Math.min(100, (Math.min(exp.control_obs_count, exp.experiment_obs_count) / exp.min_obs_per_cohort) * 100)
            : 0;
          const badge = exp.conclusion === "promoted" ? "✅" : exp.conclusion === "rejected" ? "❌" : exp.status === "cancelled" ? "⚪" : "🧪";

          return (
            <div key={exp.id} style={cardStyle}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: "6px" }}>
                <div>
                  <span style={{ marginRight: "6px" }}>{badge}</span>
                  <strong style={{ color: "#e0e0e8" }}>{exp.param_key}: {exp.baseline_value} → {exp.proposed_value}</strong>
                  {exp.conclusion && <span style={{ color: "#8888a0", marginLeft: "8px", fontSize: "0.8rem", textTransform: "capitalize" }}>{exp.conclusion}</span>}
                </div>
                {exp.status === "active" && (
                  <div style={{ display: "flex", gap: "6px" }}>
                    <button style={btnSmallStyle} onClick={() => setConfirm({ id: exp.id, action: "conclude" })}>Force Conclude</button>
                    <button style={{ ...btnSmallStyle, color: "#ff6c8a" }} onClick={() => setConfirm({ id: exp.id, action: "cancel" })}>Cancel</button>
                  </div>
                )}
              </div>

              <div style={{ color: "#5a5a6e", fontSize: "0.8rem", marginBottom: "6px" }}>
                Started: {new Date(exp.created_at).toLocaleDateString()}
                {exp.status === "active" && ` · Split: 80/20`}
              </div>

              {exp.status === "active" && (
                <>
                  <div style={{ color: "#8888a0", fontSize: "0.8rem", marginBottom: "4px" }}>
                    Control: {exp.control_obs_count} obs (avg {exp.control_mean_score.toFixed(2)}) · Treatment: {exp.experiment_obs_count} obs (avg {exp.experiment_mean_score.toFixed(2)})
                  </div>
                  <ProgressBar value={progress} label={`${Math.min(exp.experiment_obs_count, exp.min_obs_per_cohort)}/${exp.min_obs_per_cohort} min obs`} />
                  {exp.estimated_completion && (
                    <div style={{ color: "#5a5a6e", fontSize: "0.75rem", marginTop: "4px" }}>Est. completion: {new Date(exp.estimated_completion).toLocaleDateString()}</div>
                  )}
                </>
              )}

              {exp.status === "concluded" && (
                <div style={{ color: "#8888a0", fontSize: "0.8rem" }}>
                  {exp.p_value !== undefined && `p=${exp.p_value.toFixed(2)} · `}
                  Control {exp.control_mean_score.toFixed(2)} (n={exp.control_obs_count}) → Treatment {exp.experiment_mean_score.toFixed(2)} (n={exp.experiment_obs_count})
                </div>
              )}

              {exp.status === "cancelled" && exp.cancel_reason && (
                <div style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>Reason: {exp.cancel_reason}</div>
              )}
            </div>
          );
        })
      )}

      {data && <Pagination page={data.page} totalPages={data.pages} onPageChange={setPage} />}

      <ConfirmDialog
        open={confirm?.action === "conclude"}
        title="Force conclude this experiment?"
        message="The t-test will run on whatever data exists. Results may be inconclusive with few observations."
        confirmLabel="Conclude"
        onConfirm={handleConfirm}
        onCancel={() => setConfirm(null)}
      />
      <ConfirmDialog
        open={confirm?.action === "cancel"}
        title="Cancel this experiment?"
        message="Existing data will be preserved but no new observations will be collected."
        confirmLabel="Cancel Experiment"
        onConfirm={handleConfirm}
        onCancel={() => setConfirm(null)}
        showTextarea
        textareaPlaceholder="Optional reason..."
      />
    </div>
  );
}

const cardStyle: React.CSSProperties = { backgroundColor: "#12121a", border: "1px solid #1e1e2e", borderRadius: "12px", padding: "16px" };
const btnSmallStyle: React.CSSProperties = { background: "none", border: "1px solid #2a2a3e", borderRadius: "6px", padding: "4px 10px", color: "#8888a0", fontSize: "0.8rem", cursor: "pointer" };
