import React, { useEffect, useState, useCallback, useRef } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

interface DeploymentTimelineProps {
  environments: Array<{ name: string; display_name: string; order?: number }>;
  timeRange?: { start: Date; end: Date };
  filterScript?: string;
}

interface Receipt {
  id: string;
  environment: string;
  script_name: string;
  status: "success" | "failed" | "rolled_back" | "in_progress";
  started_at: string;
  finished_at?: string;
  duration_seconds?: number;
  agent_name?: string;
  commit_sha?: string;
  component_id?: string;
}

interface Component {
  id: string;
  name: string;
  display_name: string;
  icon: string | null;
}

const STATUS_COLORS: Record<string, string> = {
  success: "#6cffa0",
  failed: "#ff6c8a",
  rolled_back: "#ffcc6c",
  in_progress: "#6c8aff",
};

const TIME_RANGES = [
  { label: "Last 24h", days: 1 },
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
];

export default function DeploymentTimeline({ environments, timeRange, filterScript }: DeploymentTimelineProps) {
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const [scripts, setScripts] = useState<string[]>([]);
  const [selectedScript, setSelectedScript] = useState<string>(filterScript || "");
  const [selectedRange, setSelectedRange] = useState(7);
  const [hoveredReceipt, setHoveredReceipt] = useState<Receipt | null>(null);
  const [hoverPos, setHoverPos] = useState({ x: 0, y: 0 });
  const svgRef = useRef<SVGSVGElement>(null);
  const [components, setComponents] = useState<Component[]>([]);
  const [selectedComponent, setSelectedComponent] = useState<string>("");

  const now = timeRange?.end || new Date();
  const rangeStart = timeRange?.start || new Date(now.getTime() - selectedRange * 86400000);

  const fetchReceipts = useCallback(async () => {
    try {
      const res = await apiFetch(`${GATEWAY_API}/deployments/receipts?limit=100`);
      if (res.ok) {
        const data: Receipt[] = await res.json();
        setReceipts(data);
        const uniqueScripts = [...new Set(data.map((r) => r.script_name).filter(Boolean))];
        setScripts(uniqueScripts);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchReceipts(); }, [fetchReceipts]);

  useEffect(() => {
    apiFetch(`${GATEWAY_API}/deployments/components`)
      .then(r => r.ok ? r.json() : [])
      .then(data => setComponents(Array.isArray(data) ? data : data.components || []))
      .catch(() => {});
  }, []);

  const sortedEnvs = [...environments].sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
  const envNames = sortedEnvs.map((e) => e.name);

  const filtered = receipts.filter((r) => {
    if (selectedScript && r.script_name !== selectedScript) return false;
    if (selectedComponent && r.component_id !== selectedComponent) return false;
    const t = new Date(r.started_at).getTime();
    return t >= rangeStart.getTime() && t <= now.getTime();
  });

  // SVG layout
  const margin = { top: 40, right: 30, bottom: 30, left: 120 };
  const width = 900;
  const laneHeight = 60;
  const height = margin.top + sortedEnvs.length * laneHeight + margin.bottom;
  const plotWidth = width - margin.left - margin.right;
  const totalMs = now.getTime() - rangeStart.getTime();

  const xScale = (date: string) => {
    const ms = new Date(date).getTime() - rangeStart.getTime();
    return margin.left + (ms / totalMs) * plotWidth;
  };

  const yScale = (env: string) => {
    const idx = envNames.indexOf(env);
    return margin.top + (idx >= 0 ? idx : 0) * laneHeight + laneHeight / 2;
  };

  // Time axis ticks
  const tickCount = Math.min(selectedRange, 10);
  const tickInterval = totalMs / tickCount;
  const ticks = Array.from({ length: tickCount + 1 }, (_, i) => {
    const t = new Date(rangeStart.getTime() + i * tickInterval);
    return { x: margin.left + (i / tickCount) * plotWidth, label: formatDate(t, selectedRange) };
  });

  function formatDate(d: Date, rangeDays: number): string {
    if (rangeDays <= 1) return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    return `${d.getMonth() + 1}/${d.getDate()}`;
  }

  const handleMouseMove = (e: React.MouseEvent, r: Receipt) => {
    const rect = svgRef.current?.getBoundingClientRect();
    if (rect) setHoverPos({ x: e.clientX - rect.left + 10, y: e.clientY - rect.top - 10 });
    setHoveredReceipt(r);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>Deployment Timeline</h3>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          {components.length > 0 && (
            <select value={selectedComponent} onChange={(e) => setSelectedComponent(e.target.value)} style={styles.select}>
              <option value="">All Components</option>
              {components.map(c => <option key={c.id} value={c.id}>{c.icon ? `${c.icon} ` : ""}{c.display_name || c.name}</option>)}
            </select>
          )}
          <select
            value={selectedScript}
            onChange={(e) => setSelectedScript(e.target.value)}
            style={styles.select}
          >
            <option value="">All Scripts</option>
            {scripts.map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
          <div style={{ display: "flex", gap: "4px" }}>
            {TIME_RANGES.map((tr) => (
              <button
                key={tr.days}
                onClick={() => setSelectedRange(tr.days)}
                style={{
                  ...styles.rangeBtn,
                  backgroundColor: selectedRange === tr.days ? "#6c8aff" : "#2a2a3e",
                  color: selectedRange === tr.days ? "#fff" : "#e0e0e8",
                }}
              >
                {tr.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div style={{ position: "relative", backgroundColor: "#1a1a2e", borderRadius: "8px", borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e", overflow: "hidden" }}>
        <svg ref={svgRef} width={width} height={height} style={{ display: "block" }}>
          {/* Environment lane backgrounds */}
          {sortedEnvs.map((env, i) => (
            <rect
              key={env.name}
              x={margin.left}
              y={margin.top + i * laneHeight}
              width={plotWidth}
              height={laneHeight}
              fill={i % 2 === 0 ? "#1a1a2e" : "#1e1e32"}
            />
          ))}

          {/* Environment labels */}
          {sortedEnvs.map((env, i) => (
            <text
              key={env.name}
              x={margin.left - 10}
              y={margin.top + i * laneHeight + laneHeight / 2}
              textAnchor="end"
              dominantBaseline="middle"
              fill="#8888a0"
              fontSize="12"
            >
              {env.display_name}
            </text>
          ))}

          {/* Grid lines */}
          {ticks.map((tick, i) => (
            <g key={i}>
              <line x1={tick.x} y1={margin.top} x2={tick.x} y2={height - margin.bottom} stroke="#2a2a3e" strokeWidth="1" />
              <text x={tick.x} y={height - 10} textAnchor="middle" fill="#8888a0" fontSize="10">{tick.label}</text>
            </g>
          ))}

          {/* Deployment dots */}
          {filtered.map((r) => (
            <circle
              key={r.id}
              cx={xScale(r.started_at)}
              cy={yScale(r.environment)}
              r={6}
              fill={STATUS_COLORS[r.status] || "#8888a0"}
              stroke="#1a1a2e"
              strokeWidth="2"
              style={{ cursor: "pointer" }}
              onMouseMove={(e) => handleMouseMove(e, r)}
              onMouseLeave={() => setHoveredReceipt(null)}
            />
          ))}
        </svg>

        {/* Hover tooltip */}
        {hoveredReceipt && (
          <div style={{
            position: "absolute",
            left: hoverPos.x,
            top: hoverPos.y,
            backgroundColor: "#2a2a3e",
            borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
            borderRadius: "6px",
            padding: "10px",
            fontSize: "0.8rem",
            color: "#e0e0e8",
            pointerEvents: "none",
            zIndex: 10,
            minWidth: "200px",
          }}>
            <div style={{ fontWeight: 600, color: STATUS_COLORS[hoveredReceipt.status], marginBottom: "4px" }}>
              {hoveredReceipt.status.replace("_", " ").toUpperCase()}
            </div>
            <div>Script: {hoveredReceipt.script_name}</div>
            {hoveredReceipt.component_id && (() => { const c = components.find(c => c.id === hoveredReceipt.component_id); return c ? <div>Component: {c.icon || ""}{c.display_name || c.name}</div> : null; })()}
            <div>Started: {new Date(hoveredReceipt.started_at).toLocaleString()}</div>
            {hoveredReceipt.duration_seconds != null && <div>Duration: {hoveredReceipt.duration_seconds}s</div>}
            {hoveredReceipt.agent_name && <div>Agent: {hoveredReceipt.agent_name}</div>}
            {hoveredReceipt.commit_sha && <div>SHA: {hoveredReceipt.commit_sha.slice(0, 8)}</div>}
          </div>
        )}
      </div>

      {filtered.length === 0 && (
        <div style={{ color: "#8888a0", fontSize: "0.85rem", textAlign: "center", padding: "12px" }}>
          No deployments found in the selected time range.
        </div>
      )}

      {/* Legend */}
      <div style={{ display: "flex", gap: "16px", justifyContent: "center" }}>
        {Object.entries(STATUS_COLORS).map(([status, color]) => (
          <div key={status} style={{ display: "flex", alignItems: "center", gap: "4px", fontSize: "0.8rem", color: "#8888a0" }}>
            <div style={{ width: 10, height: 10, borderRadius: "50%", backgroundColor: color }} />
            {status.replace("_", " ")}
          </div>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  select: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "6px",
    padding: "6px 10px",
    fontSize: "0.8rem",
  },
  rangeBtn: {
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: "6px",
    padding: "4px 10px",
    fontSize: "0.75rem",
    cursor: "pointer",
  },
};
