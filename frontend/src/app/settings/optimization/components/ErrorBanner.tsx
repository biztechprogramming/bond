"use client";

import React from "react";

interface ErrorBannerProps {
  message: string;
  onRetry?: () => void;
}

export default function ErrorBanner({ message, onRetry }: ErrorBannerProps) {
  return (
    <div style={{ backgroundColor: "#2a1a1e", border: "1px solid #5a2a2e", borderRadius: "8px", padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
      <span style={{ color: "#ff6c8a", fontSize: "0.85rem" }}>{message}</span>
      {onRetry && (
        <button onClick={onRetry} style={{ backgroundColor: "#3a1a1e", color: "#ff6c8a", border: "1px solid #5a2a2e", borderRadius: "6px", padding: "6px 12px", fontSize: "0.8rem", cursor: "pointer", whiteSpace: "nowrap" }}>Retry</button>
      )}
    </div>
  );
}
