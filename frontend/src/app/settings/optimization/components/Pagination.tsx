"use client";

import React from "react";

interface PaginationProps {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}

export default function Pagination({ page, totalPages, onPageChange }: PaginationProps) {
  if (totalPages <= 1) return null;
  const btnStyle: React.CSSProperties = { background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "6px", padding: "6px 12px", color: "#8888a0", fontSize: "0.8rem", cursor: "pointer" };
  const disabledStyle: React.CSSProperties = { ...btnStyle, opacity: 0.4, cursor: "default" };

  return (
    <div style={{ display: "flex", alignItems: "center", gap: "12px", justifyContent: "center", marginTop: "16px" }}>
      <button style={page <= 1 ? disabledStyle : btnStyle} disabled={page <= 1} onClick={() => onPageChange(page - 1)}>Prev</button>
      <span style={{ color: "#8888a0", fontSize: "0.85rem" }}>Page {page} of {totalPages}</span>
      <button style={page >= totalPages ? disabledStyle : btnStyle} disabled={page >= totalPages} onClick={() => onPageChange(page + 1)}>Next</button>
    </div>
  );
}
