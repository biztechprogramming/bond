"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";
import LineChart from "./charts/LineChart";
import LoadingSkeleton from "./components/LoadingSkeleton";
import ErrorBanner from "./components/ErrorBanner";
import EmptyState from "./components/EmptyState";

const API = `${BACKEND_API}/optimization`;

interface Overview {
  period_days: number;
  total_observations: number;
  avg_score_7d: number;
  avg_score_30d: number;
  avg_score_prev_30d: number;
  score_trend: string;
  pending_lessons: number;
  approved_lessons: number;
  active_experiments: number;
  concluded_experiments: number;
  failure_signals: Record<string, { count: number; pct: number }>;
  categories: Record<string, number>;
}

interface DayData {
  date: string;
  avg_score: number;
  turn_count: number;
  avg_cost: number;
}

type Period = 7 | 30 | 90;

export default function OverviewPanel() {
  const [period, setPeriod] = useState<Period>(30);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [days, setDays] = useState<DayData[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const fetchData = useCallback(async () => {
    try {
      setError("");
      const [ovRes, outRes] = await Promise.all([
        fetch(`${API}/overview?days=${period}`),
        fetch(`${API}/outcomes?days=${period}`),
      ]);
      if (!ovRes.ok || !outRes.ok) throw new Error("Failed to load");
      setOverview(await ovRes.json());
      const outData = await outRes.json();
      setDays(outData.days || []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load overview");
    } finally {
      setLoading(false);
    }
  }, [period]);

  useEffect(() => { setLoading(true); fetchData(); }, [fetchData]);

  // Auto-refresh every 60s
  useEffect(() => {
    const id = setInterval(fetchData, 60000);
    return () => clearInterval(id);
  }, [fetchData]);

  if (loading) return <LoadingSkeleton lines={6} />;
  if (error) return <ErrorBanner message={error} onRetry={fetchData} />;
  if (!overview) return <EmptyState message="No data available" />;

  if (overview.total_observations < 50) {
    return (
      <EmptyState
        icon="📊"
        message={`Collecting data... ${overview.total_observations}/50 observations recorded. Charts will appear once enough data is available.`}
      />
    );
  }

  const trendArrow = overview.score_trend === "improving" ? " ↑" : overview.score_trend === "declining" ? " ↓" : "";
  const trendColor = overview.score_trend === "improving" ? "#6cffa0" : overview.score_trend === "declining" ? "#ff6c8a" : "#8888a0";

  const signalLabels: Record<string, string> = {
    loop_interventions: "Loop interventions",
    user_corrections: "User corrections",
    continuations: "Continuations",
    compressions: "Compressions",
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Period selector */}
      <div style={{ display: "flex", gap: "4px" }}>
        {([7, 30, 90] as Period[]).map((p) => (
          <button key={p} onClick={() => setPeriod(p)} style={{
            background: period === p ? "#6c8aff" : "none",
            color: period === p ? "#fff" : "#8888a0",
            border: period === p ? "none" : "1px solid #2a2a3e",
            borderRadius: "6px", padding: "6px 14px", fontSize: "0.8rem", cursor: "pointer", fontWeight: period === p ? 600 : 400,
          }}>{p}d</button>
        ))}
      </div>

      {/* Stats cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: "12px" }}>
        <StatCard label="Avg Score" value={overview.avg_score_7d.toFixed(2)} suffix={trendArrow} color={trendColor} />
        <StatCard label="Turns" value={overview.total_observations.toLocaleString()} />
        <StatCard label="Pending Lessons" value={String(overview.pending_lessons)} color={overview.pending_lessons > 0 ? "#ffcc44" : undefined} />
        <StatCard label="Active Experiments" value={String(overview.active_experiments)} color={overview.active_experiments > 0 ? "#6c8aff" : undefined} />
      </div>

      {/* Score trend chart */}
      <div style={cardStyle}>
        <h3 style={chartTitleStyle}>Outcome Score Trend ({period} days)</h3>
        <LineChart
          data={days.map((d) => ({ x: d.date, y: d.avg_score }))}
          color="#6cffa0"
          fillColor="#6cffa020"
          title={`Outcome Score Trend (${period} days)`}
        />
      </div>

      {/* Cost chart */}
      <div style={cardStyle}>
        <h3 style={chartTitleStyle}>Cost per Turn ({period} days)</h3>
        <LineChart
          data={days.map((d) => ({ x: d.date, y: d.avg_cost }))}
          color="#ffcc44"
          fillColor="#ffcc4420"
          title={`Cost per Turn (${period} days)`}
        />
      </div>

      {/* Failure signals */}
      <div style={cardStyle}>
        <h3 style={chartTitleStyle}>Failure Signals</h3>
        <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
          {Object.entries(overview.failure_signals).map(([key, val]) => (
            <div key={key} style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "6px 0", borderBottom: "1px solid #1e1e2e" }}>
              <span style={{ color: "#e0e0e8", fontSize: "0.85rem" }}>{signalLabels[key] || key}</span>
              <span style={{ color: "#8888a0", fontSize: "0.85rem" }}>{val.count} ({val.pct.toFixed(1)}%)</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function StatCard({ label, value, suffix, color }: { label: string; value: string; suffix?: string; color?: string }) {
  return (
    <div style={{ backgroundColor: "#12121a", border: "1px solid #1e1e2e", borderRadius: "10px", padding: "16px" }}>
      <div style={{ color: "#5a5a6e", fontSize: "0.75rem", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.5px" }}>{label}</div>
      <div style={{ color: color || "#e0e0e8", fontSize: "1.4rem", fontWeight: 700 }}>{value}{suffix}</div>
    </div>
  );
}

const cardStyle: React.CSSProperties = { backgroundColor: "#12121a", border: "1px solid #1e1e2e", borderRadius: "12px", padding: "16px" };
const chartTitleStyle: React.CSSProperties = { color: "#8888a0", fontSize: "0.85rem", fontWeight: 500, margin: "0 0 12px 0" };
