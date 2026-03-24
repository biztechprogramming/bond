"use client";

import React, { useEffect, useRef, useState } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: (textareaValue?: string) => void;
  onCancel: () => void;
  showTextarea?: boolean;
  textareaPlaceholder?: string;
}

export default function ConfirmDialog({ open, title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", onConfirm, onCancel, showTextarea, textareaPlaceholder }: ConfirmDialogProps) {
  const confirmRef = useRef<HTMLButtonElement>(null);
  const [text, setText] = useState("");

  useEffect(() => {
    if (open) {
      setText("");
      setTimeout(() => confirmRef.current?.focus(), 50);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onCancel(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  if (!open) return null;

  return (
    <div style={{ position: "fixed", inset: 0, backgroundColor: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000 }} onClick={onCancel}>
      <div style={{ backgroundColor: "#12121a", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "12px", padding: "24px", maxWidth: "440px", width: "90%" }} onClick={(e) => e.stopPropagation()}>
        <h3 style={{ color: "#e0e0e8", fontSize: "1rem", margin: "0 0 8px 0" }}>{title}</h3>
        <p style={{ color: "#8888a0", fontSize: "0.9rem", margin: "0 0 16px 0" }}>{message}</p>
        {showTextarea && (
          <textarea
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder={textareaPlaceholder}
            style={{ width: "100%", boxSizing: "border-box", backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px", color: "#e0e0e8", fontSize: "0.85rem", outline: "none", resize: "vertical", minHeight: "60px", marginBottom: "16px", fontFamily: "inherit" }}
          />
        )}
        <div style={{ display: "flex", gap: "8px", justifyContent: "flex-end" }}>
          <button onClick={onCancel} style={{ backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" }}>{cancelLabel}</button>
          <button ref={confirmRef} onClick={() => onConfirm(showTextarea ? text : undefined)} style={{ backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" }}>{confirmLabel}</button>
        </div>
      </div>
    </div>
  );
}
