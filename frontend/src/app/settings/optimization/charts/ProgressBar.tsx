"use client";

import React from "react";

interface ProgressBarProps {
  value: number;
  color?: string;
  label?: string;
}

export default function ProgressBar({ value, color = "#6c8aff", label }: ProgressBarProps) {
  const clamped = Math.max(0, Math.min(100, value));
  return (
    <div>
      {label && <div style={{ fontSize: "0.8rem", color: "#8888a0", marginBottom: "4px" }}>{label}</div>}
      <div style={{ width: "100%", height: "8px", backgroundColor: "#1e1e2e", borderRadius: "4px", overflow: "hidden" }}>
        <div style={{ width: `${clamped}%`, height: "100%", backgroundColor: color, borderRadius: "4px", transition: "width 0.3s ease" }} />
      </div>
    </div>
  );
}
