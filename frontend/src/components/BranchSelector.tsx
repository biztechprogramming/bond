"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";

interface BranchInfo {
  name: string;
  lastCommit: string;
}

interface BranchStatus {
  container_id: string;
  branch: string;
  worker_online: boolean;
  worker_branch: string | null;
  active_turns: number | null;
  pending_reload: boolean;
}

interface BranchSelectorProps {
  branchChangedSignal: number;
  turnCompleted: number;
}

export default function BranchSelector({ branchChangedSignal, turnCompleted }: BranchSelectorProps) {
  const [open, setOpen] = useState(false);
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [status, setStatus] = useState<BranchStatus | null>(null);
  const [switching, setSwitching] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const resp = await fetch(`${GATEWAY_API}/container/branch`);
      if (resp.ok) setStatus(await resp.json());
    } catch { /* ignore */ }
  }, []);

  const fetchBranches = useCallback(async () => {
    try {
      const resp = await fetch(`${GATEWAY_API}/container/branches`);
      if (resp.ok) {
        const data = await resp.json();
        setBranches(data.branches || []);
      }
    } catch { /* ignore */ }
  }, []);

  // Fetch status on mount and when signals change
  useEffect(() => { fetchStatus(); }, [fetchStatus, branchChangedSignal, turnCompleted]);

  // Fetch branches when dropdown opens
  useEffect(() => {
    if (open) fetchBranches();
  }, [open, fetchBranches]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const switchBranch = async (branch: string) => {
    if (switching) return;
    setSwitching(true);
    try {
      const resp = await fetch(`${GATEWAY_API}/container/branch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch }),
      });
      if (resp.ok) {
        await fetchStatus();
      }
    } catch { /* ignore */ }
    setSwitching(false);
    setOpen(false);
  };

  const currentBranch = status?.branch || "main";
  const disabled = (status?.active_turns ?? 0) > 0;
  const workerOffline = status !== null && !status.worker_online;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => !disabled && setOpen(!open)}
        disabled={disabled}
        title={
          disabled
            ? "Another conversation is in progress \u2014 branch switching unavailable until it completes."
            : `Current branch: ${currentBranch}`
        }
        style={{
          backgroundColor: "#1e1e2e",
          border: "1px solid #2a2a3e",
          borderRadius: "8px",
          padding: "4px 10px",
          color: disabled ? "#5a5a6e" : "#e0e0e8",
          fontSize: "0.8rem",
          cursor: disabled ? "not-allowed" : "pointer",
          display: "flex",
          alignItems: "center",
          gap: "6px",
          fontFamily: "monospace",
          opacity: disabled ? 0.6 : 1,
        }}
      >
        <span style={{ fontSize: "0.85rem" }}>{"\uD83D\uDD00"}</span>
        {currentBranch}
        {workerOffline && (
          <span
            title="Worker offline \u2014 branch will apply on next message"
            style={{
              width: "6px",
              height: "6px",
              borderRadius: "50%",
              backgroundColor: "#ff6c8a",
              display: "inline-block",
              flexShrink: 0,
            }}
          />
        )}
        {status?.pending_reload && (
          <span style={{ fontSize: "0.7rem", color: "#ffa06c" }}>*</span>
        )}
      </button>

      {open && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 4px)",
            left: 0,
            minWidth: "220px",
            backgroundColor: "#1e1e2e",
            border: "1px solid #2a2a3e",
            borderRadius: "10px",
            overflow: "hidden",
            zIndex: 100,
            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
          }}
        >
          {branches.length === 0 && (
            <div style={{ padding: "12px 14px", color: "#5a5a6e", fontSize: "0.8rem" }}>
              Loading branches...
            </div>
          )}
          {branches.map((b) => (
            <div
              key={b.name}
              onClick={() => b.name !== currentBranch && switchBranch(b.name)}
              style={{
                padding: "10px 14px",
                cursor: b.name === currentBranch ? "default" : "pointer",
                fontSize: "0.83rem",
                fontFamily: "monospace",
                color: b.name === currentBranch ? "#6c8aff" : "#e0e0e8",
                backgroundColor: b.name === currentBranch ? "#12121a" : "transparent",
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                transition: "background-color 0.15s",
              }}
              onMouseEnter={(e) => {
                if (b.name !== currentBranch)
                  (e.currentTarget as HTMLElement).style.backgroundColor = "#2a2a3e";
              }}
              onMouseLeave={(e) => {
                if (b.name !== currentBranch)
                  (e.currentTarget as HTMLElement).style.backgroundColor = "transparent";
              }}
            >
              <span>{b.name}</span>
              {b.name === currentBranch && (
                <span style={{ color: "#6c8aff", fontSize: "0.7rem" }}>current</span>
              )}
            </div>
          ))}
          {workerOffline && (
            <div
              style={{
                padding: "8px 14px",
                borderTop: "1px solid #2a2a3e",
                color: "#8888a0",
                fontSize: "0.72rem",
              }}
            >
              Worker offline \u2014 preference saved for next startup
            </div>
          )}
          {status?.container_id && status.container_id !== "default" && (
            <div
              style={{
                padding: "6px 14px",
                borderTop: "1px solid #2a2a3e",
                color: "#5a5a6e",
                fontSize: "0.7rem",
              }}
            >
              Container: {status.container_id}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
