"use client";

import React, { useState, useRef } from "react";

interface BarData {
  label: string;
  value: number;
}

interface BarChartProps {
  data: BarData[];
  color: string;
  width?: number;
  height?: number;
  title: string;
}

export default function BarChart({ data, color, width = 600, height = 200, title }: BarChartProps) {
  const [tooltip, setTooltip] = useState<{ x: number; y: number; label: string; value: number } | null>(null);
  const svgRef = useRef<SVGSVGElement>(null);

  if (data.length === 0) return null;

  const pad = { top: 20, right: 20, bottom: 30, left: 50 };
  const w = width - pad.left - pad.right;
  const h = height - pad.top - pad.bottom;
  const maxVal = Math.max(...data.map((d) => d.value), 1);
  const barW = Math.max(w / data.length - 4, 2);

  const handleMouse = (e: React.MouseEvent<SVGSVGElement>) => {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const mx = ((e.clientX - rect.left) / rect.width) * width;
    const idx = Math.floor(((mx - pad.left) / w) * data.length);
    if (idx >= 0 && idx < data.length) {
      setTooltip({ x: e.clientX - rect.left, y: e.clientY - rect.top - 40, label: data[idx].label, value: data[idx].value });
    }
  };

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
        <desc>Bar chart showing {data.length} items for {title}</desc>

        {data.map((d, i) => {
          const barH = (d.value / maxVal) * h;
          const x = pad.left + (i / data.length) * w + 2;
          const y = pad.top + h - barH;
          return (
            <rect key={i} x={x} y={y} width={barW} height={barH} fill={color} rx="2" />
          );
        })}

        {/* X labels (show first, mid, last) */}
        {data.length > 0 && (
          <>
            <text x={pad.left} y={height - 6} fill="#5a5a6e" fontSize="10" textAnchor="start">{data[0].label}</text>
            {data.length > 2 && <text x={pad.left + w / 2} y={height - 6} fill="#5a5a6e" fontSize="10" textAnchor="middle">{data[Math.floor(data.length / 2)].label}</text>}
            <text x={pad.left + w} y={height - 6} fill="#5a5a6e" fontSize="10" textAnchor="end">{data[data.length - 1].label}</text>
          </>
        )}
      </svg>

      {tooltip && (
        <div style={{
          position: "absolute", left: tooltip.x, top: tooltip.y, transform: "translateX(-50%)",
          backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "6px",
          padding: "4px 8px", fontSize: "0.75rem", color: "#e0e0e8", pointerEvents: "none", whiteSpace: "nowrap", zIndex: 10,
        }}>
          {tooltip.label}: {tooltip.value}
        </div>
      )}

      <table style={{ position: "absolute", width: 1, height: 1, overflow: "hidden", clip: "rect(0,0,0,0)" }}>
        <caption>{title}</caption>
        <thead><tr><th>Label</th><th>Value</th></tr></thead>
        <tbody>
          {data.map((d, i) => <tr key={i}><td>{d.label}</td><td>{d.value}</td></tr>)}
        </tbody>
      </table>
    </div>
  );
}
