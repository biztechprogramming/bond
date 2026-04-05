"use client";
import React, { useState, useCallback } from "react";
import { rewriteImageSrc } from "@/lib/image-utils";

export interface ImageCardProps {
  src: string;
  prompt: string;
  revisedPrompt?: string;
  provider: string;
  model: string;
  size: string;
  cost?: number;
  onExpand: () => void;
}

export default function ImageCard({
  src,
  prompt,
  revisedPrompt,
  provider,
  model,
  size,
  cost,
  onExpand,
}: ImageCardProps) {
  const [hovered, setHovered] = useState(false);
  const resolvedSrc = rewriteImageSrc(src);

  const handleDownload = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      const a = document.createElement("a");
      a.href = resolvedSrc;
      a.download = src.split("/").pop() || "image.png";
      a.click();
    },
    [resolvedSrc, src]
  );

  const handleCopyPrompt = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      navigator.clipboard.writeText(prompt);
    },
    [prompt]
  );

  const handleRegenerate = useCallback(
    (e: React.MouseEvent) => {
      e.stopPropagation();
      navigator.clipboard.writeText(prompt);
    },
    [prompt]
  );

  const providerLabel =
    provider === "openai" ? "OpenAI" : provider === "replicate" ? "Replicate" : provider === "comfyui" ? "ComfyUI" : provider;

  return (
    <div
      style={styles.container}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      onClick={onExpand}
    >
      <div style={styles.imageWrapper}>
        <img src={resolvedSrc} alt={prompt} style={styles.image} />
        {cost != null && cost > 0 && (
          <span style={styles.costBadge}>${cost.toFixed(2)}</span>
        )}
        {hovered && (
          <div style={styles.overlay}>
            <button style={styles.overlayBtn} onClick={handleDownload} title="Download">
              ⬇
            </button>
            <button style={styles.overlayBtn} onClick={handleCopyPrompt} title="Copy prompt">
              📋
            </button>
            <button style={styles.overlayBtn} onClick={handleRegenerate} title="Regenerate (copies prompt)">
              🔄
            </button>
          </div>
        )}
      </div>
      <div style={styles.metaBar}>
        <span style={styles.providerName}>{providerLabel}</span>
        <span style={styles.metaDivider}>·</span>
        <span style={styles.metaText}>{model}</span>
        <span style={styles.metaDivider}>·</span>
        <span style={styles.metaText}>{size}</span>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    cursor: "pointer",
    borderRadius: "10px",
    overflow: "hidden",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "#2a2a3e",
    backgroundColor: "#12121a",
  },
  imageWrapper: {
    position: "relative",
    overflow: "hidden",
  },
  image: {
    display: "block",
    width: "100%",
    height: "auto",
    borderRadius: "10px 10px 0 0",
  },
  costBadge: {
    position: "absolute",
    top: "8px",
    right: "8px",
    backgroundColor: "rgba(0,0,0,0.7)",
    color: "#6cffa0",
    fontSize: "0.72rem",
    fontWeight: 600,
    padding: "2px 8px",
    borderRadius: "6px",
  },
  overlay: {
    position: "absolute",
    inset: "0",
    backgroundColor: "rgba(0,0,0,0.55)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "12px",
  },
  overlayBtn: {
    background: "rgba(255,255,255,0.15)",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "rgba(255,255,255,0.2)",
    borderRadius: "8px",
    color: "#fff",
    fontSize: "1.1rem",
    padding: "8px 12px",
    cursor: "pointer",
  },
  metaBar: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    padding: "6px 10px",
    fontSize: "0.75rem",
    color: "#8888a0",
  },
  providerName: {
    color: "#6c8aff",
    fontWeight: 600,
  },
  metaDivider: {
    color: "#3a3a4e",
  },
  metaText: {
    color: "#8888a0",
  },
};
