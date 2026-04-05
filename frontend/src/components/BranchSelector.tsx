"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

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
  agentId?: string | null;
}

export default function BranchSelector({ branchChangedSignal, turnCompleted, agentId }: BranchSelectorProps) {
  const [open, setOpen] = useState(false);
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [status, setStatus] = useState<BranchStatus | null>(null);
  const [switching, setSwitching] = useState(false);
  const [pendingBranch, setPendingBranch] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const params = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
      const resp = await apiFetch(`${GATEWAY_API}/container/branch${params}`);
      if (resp.ok) {
        const data = await resp.json();
        setStatus(data);
        // Clear pending branch once the container has been recreated on the new branch
        if (pendingBranch && !data.pending_reload && data.branch === pendingBranch) {
          setPendingBranch(null);
        }
      }
    } catch { /* ignore */ }
  }, [agentId, pendingBranch]);

  const fetchBranches = useCallback(async () => {
    try {
      const resp = await apiFetch(`${GATEWAY_API}/container/branches`);
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
      const resp = await apiFetch(`${GATEWAY_API}/container/branch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch, ...(agentId ? { agent_id: agentId } : {}) }),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.deferred) {
          // Turn is active — container will be recreated after it finishes
          setPendingBranch(branch);
        }
        await fetchStatus();
      }
    } catch { /* ignore */ }
    setSwitching(false);
    setOpen(false);
  };

  const currentBranch = pendingBranch || status?.branch || "main";
  const activeTurns = status?.active_turns ?? 0;
  // Allow branch selection even during active turns — the switch is deferred
  const disabled = false;
  const workerOffline = status !== null && !status.worker_online;

  return (
    <div ref={ref} style={{ position: "relative" }}>
      <button
        onClick={() => !disabled && setOpen(!open)}
        disabled={disabled}
        title={
          pendingBranch
            ? `Switching to ${pendingBranch} after current turn completes (container will be recreated)`
            : `Current branch: ${currentBranch}`
        }
        style={{
          backgroundColor: "#1e1e2e",
          borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
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
        {(status?.pending_reload || pendingBranch) && (
          <span title={`Switching to ${pendingBranch || "new branch"} after turn completes`} style={{ fontSize: "0.7rem", color: "#ffa06c" }}>⏳</span>
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
            borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
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
                borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#2a2a3e",
                color: "#8888a0",
                fontSize: "0.72rem",
              }}
            >
              Container offline \u2014 will start on selected branch
            </div>
          )}
          {status?.container_id && status.container_id !== "default" && (
            <div
              style={{
                padding: "6px 14px",
                borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#2a2a3e",
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
