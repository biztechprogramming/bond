"use client";

import React, { useState, useRef } from "react";

interface DataPoint {
  x: string;
  y: number;
}

interface LineChartProps {
  data: DataPoint[];
  color: string;
  fillColor?: string;
  width?: number;
  height?: number;
  title: string;
}

export default function LineChart({ data, color, fillColor, width = 600, height = 200, title }: LineChartProps) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; label: string; value: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (data.length === 0) return null;

  const pad = { top: 20, right: 20, bottom: 30, left: 50 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;

  const yMin = Math.min(...data.map((d) => d.y));
  const yMax = Math.max(...data.map((d) => d.y));
  const yRange = yMax - yMin || 1;

  const toX = (i: number) => pad.left + (i / Math.max(data.length - 1, 1)) * w;
  const toY = (v: number) => pad.top + h - ((v - yMin) / yRange) * h;

  const points = data.map((d, i) => `${toX(i)},${toY(d.y)}`).join(" ");
  const areaPoints = `${toX(0)},${pad.top + h} ${points} ${toX(data.length - 1)},${pad.top + h}`;

  const handleMouse = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * width;
    const idx = Math.round(((mx - pad.left) / w) * (data.length - 1));
    if (idx >= 0 && idx < data.length) {
      setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top - 40, label: data[idx].x, value: data[idx].y });
    }
  };

  // Y-axis ticks
  const yTicks = 4;
  const yTickValues = Array.from({ length: yTicks + 1 }, (_, i) => yMin + (yRange * i) / yTicks);

  return (
    <div style={{ position: "relative" }}>
      <svg
        ref={svgRef}
        viewBox={`0 0 ${width} ${height}`}
        style={{ width: "100%", height: "auto" }}
        role="img"
        aria-label={title}
        onMouseMove={handleMouse}
        onMouseLeave={() => setTooltip(null)}
      >
        <title>{title}</title>
        <desc>Line chart showing {data.length} data points for {title}</desc>

        {/* Y-axis grid + labels */}
        {yTickValues.map((v, i) => (
          <g key={i}>
            <line x1={pad.left} x2={width - pad.right} y1={toY(v)} y2={toY(v)} stroke="#2a2a3e" strokeWidth="1" />
            <text x={pad.left - 6} y={toY(v) + 4} fill="#5a5a6e" fontSize="10" textAnchor="end">{v.toFixed(2)}</text>
          </g>
        ))}

        {/* X-axis labels (first, mid, last) */}
        {data.length > 0 && (
          <>
            <text x={toX(0)} y={height - 6} fill="#5a5a6e" fontSize="10" textAnchor="start">{data[0].x}</text>
            {data.length > 2 && <text x={toX(Math.floor(data.length / 2))} y={height - 6} fill="#5a5a6e" fontSize="10" textAnchor="middle">{data[Math.floor(data.length / 2)].x}</text>}
            <text x={toX(data.length - 1)} y={height - 6} fill="#5a5a6e" fontSize="10" textAnchor="end">{data[data.length - 1].x}</text>
          </>
        )}

        {/* Area fill */}
        {fillColor && <polygon points={areaPoints} fill={fillColor} />}

        {/* Line */}
        <polyline points={points} fill="none" stroke={color} strokeWidth="2" strokeLinejoin="round" />

        {/* Data point dots */}
        {data.map((d, i) => (
          <circle key={i} cx={toX(i)} cy={toY(d.y)} r="3" fill={color} />
        ))}
      </svg>

      {/* Tooltip */}
      {tooltip && (
        <div style={{
          position: "absolute", left: tooltip.x, top: tooltip.y, transform: "translateX(-50%)",
          backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: "6px",
          padding: "4px 8px", fontSize: "0.75rem", color: "#e0e0e8", pointerEvents: "none", whiteSpace: "nowrap", zIndex: 10,
        }}>
          {tooltip.label}: {tooltip.value.toFixed(3)}
        </div>
      )}

      {/* Aria-live for tooltip announcements */}
      <div aria-live="polite" style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0,0,0,0)" }}>
        {tooltip && `${tooltip.label}: ${tooltip.value.toFixed(3)}`}
      </div>

      {/* Hidden data table for screen readers */}
      <table style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0,0,0,0)" }}>
        <caption>{title}</caption>
        <thead><tr><th>Date</th><th>Value</th></tr></thead>
        <tbody>
          {data.map((d, i) => <tr key={i}><td>{d.x}</td><td>{d.y}</td></tr>)}
        </tbody>
      </table>
    </div>
  );
}
