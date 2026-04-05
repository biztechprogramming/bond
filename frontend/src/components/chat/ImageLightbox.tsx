"use client";
import React, { useEffect, useRef, useCallback } from "react";
import { rewriteImageSrc } from "@/lib/image-utils";

export interface ImageLightboxProps {
  src: string;
  prompt: string;
  revisedPrompt?: string;
  provider: string;
  model: string;
  size: string;
  cost?: number;
  onClose: () => void;
  onDownload: () => void;
  onRegenerate: () => void;
}

export default function ImageLightbox({
  src,
  prompt,
  revisedPrompt,
  provider,
  model,
  size,
  cost,
  onClose,
  onDownload,
  onRegenerate,
}: ImageLightboxProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const resolvedSrc = rewriteImageSrc(src);

  useEffect(() => {
    containerRef.current?.focus();
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );

  const handleBackdropClick = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose]
  );

  const handleCopyPrompt = useCallback(() => {
    navigator.clipboard.writeText(prompt);
  }, [prompt]);

  const providerLabel =
    provider === "openai" ? "OpenAI" : provider === "replicate" ? "Replicate" : provider === "comfyui" ? "ComfyUI" : provider;

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-label="Image lightbox"
      tabIndex={-1}
      style={styles.backdrop}
      onClick={handleBackdropClick}
      onKeyDown={handleKeyDown}
    >
      <style>{`
        @media (prefers-reduced-motion: reduce) {
          .lightbox-image { animation: none !important; }
        }
      `}</style>
      <button style={styles.closeBtn} onClick={onClose} aria-label="Close">
        ✕
      </button>
      <div style={styles.content}>
        <img
          className="lightbox-image"
          src={resolvedSrc}
          alt={prompt}
          style={styles.image}
        />
        <div style={styles.bottomBar}>
          <div style={styles.promptText}>
            {revisedPrompt || prompt}
          </div>
          <div style={styles.metaRow}>
            <span style={{ color: "#6c8aff", fontWeight: 600 }}>{providerLabel}</span>
            <span style={styles.metaDot}>·</span>
            <span>{model}</span>
            <span style={styles.metaDot}>·</span>
            <span>{size}</span>
            {cost != null && cost > 0 && (
              <>
                <span style={styles.metaDot}>·</span>
                <span style={{ color: "#6cffa0" }}>${cost.toFixed(2)}</span>
              </>
            )}
          </div>
          <div style={styles.actions}>
            <button style={styles.actionBtn} onClick={onDownload}>
              ⬇ Download
            </button>
            <button style={styles.actionBtn} onClick={handleCopyPrompt}>
              📋 Copy Prompt
            </button>
            <button style={styles.actionBtn} onClick={onRegenerate}>
              🔄 Regenerate
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  backdrop: {
    position: "fixed",
    inset: "0",
    backgroundColor: "rgba(0,0,0,0.85)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
    outline: "none",
  },
  closeBtn: {
    position: "absolute",
    top: "16px",
    right: "16px",
    background: "rgba(255,255,255,0.1)",
    borderWidth: 0,
    borderStyle: "none",
    borderColor: "transparent",
    color: "#fff",
    fontSize: "1.2rem",
    padding: "8px 12px",
    borderRadius: "8px",
    cursor: "pointer",
    zIndex: 1001,
  },
  content: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    maxWidth: "90vw",
    maxHeight: "90vh",
    gap: "16px",
  },
  image: {
    maxWidth: "90vw",
    maxHeight: "70vh",
    objectFit: "contain",
    borderRadius: "8px",
  },
  bottomBar: {
    display: "flex",
    flexDirection: "column",
    gap: "8px",
    maxWidth: "700px",
    width: "100%",
    padding: "0 16px",
  },
  promptText: {
    color: "#e0e0e8",
    fontSize: "0.9rem",
    lineHeight: "1.4",
    textAlign: "center",
  },
  metaRow: {
    display: "flex",
    justifyContent: "center",
    gap: "8px",
    fontSize: "0.8rem",
    color: "#8888a0",
  },
  metaDot: { color: "#3a3a4e" },
  actions: {
    display: "flex",
    justifyContent: "center",
    gap: "8px",
    marginTop: "4px",
  },
  actionBtn: {
    background: "rgba(255,255,255,0.1)",
    borderWidth: "1px",
    borderStyle: "solid",
    borderColor: "rgba(255,255,255,0.15)",
    borderRadius: "8px",
    color: "#e0e0e8",
    fontSize: "0.82rem",
    padding: "6px 14px",
    cursor: "pointer",
  },
};
