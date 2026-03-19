"use client";

import React from "react";

interface EmptyStateProps {
  message: string;
  icon?: string;
}

export default function EmptyState({ message, icon = "📭" }: EmptyStateProps) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", padding: "48px 24px", color: "#5a5a6e" }}>
      <span style={{ fontSize: "2.5rem", marginBottom: "12px" }}>{icon}</span>
      <p style={{ fontSize: "0.9rem", textAlign: "center", margin: 0 }}>{message}</p>
    </div>
  );
}
