"use client";

import React from "react";
import type { DiscoveryMode } from "@/hooks/useAgentDiscovery";

const MESSAGES: Record<string, string> = {
  "repo-only": "Could not connect to server \u2014 discovering from repository only",
  "server-only": "Could not clone repository \u2014 discovering from server only",
  "interview": "Could not access repository or server \u2014 Bond will ask you a few questions",
};

export default function DegradedModeBanner({ mode }: { mode: DiscoveryMode }) {
  if (mode === "full") return null;
  const message = MESSAGES[mode] || "Discovery running in limited mode";

  return (
    <div role="alert" style={styles.banner}>
      <span style={styles.icon}>{"\u26a0\ufe0f"}</span>
      <span>{message}</span>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  banner: {
    display: "flex",
    alignItems: "center",
    gap: 8,
    padding: "10px 16px",
    backgroundColor: "rgba(255, 204, 108, 0.1)",
    borderWidth: 1,
    borderStyle: "solid",
    borderColor: "#ffcc6c",
    borderRadius: 8,
    color: "#ffcc6c",
    fontSize: "0.85rem",
  },
  icon: { fontSize: "1rem" },
};
