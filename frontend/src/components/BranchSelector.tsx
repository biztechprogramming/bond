"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";

interface BranchInfo {
  name: string;
  last_push: string;
}

interface BranchStatus {
  branch: string;
  active_turns: number;
  pending_reload: boolean;
  container_id: string;
}

interface BranchSelectorProps {
  /** Listen for branch_changed WS messages — parent passes latest */
  branchChangedSignal?: number;
  /** Whether a turn just completed — triggers re-fetch */
  turnCompleted?: number;
}

export default function BranchSelector({ branchChangedSignal, turnCompleted }: BranchSelectorProps) {
  const [expanded, setExpanded] = useState(false);
  const [status, setStatus] = useState<BranchStatus | null>(null);
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [switching, setSwitching] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/container/status`);
      const data = await res.json();
      setStatus(data);
    } catch {
      // silently fail
    }
  }, []);

  const fetchBranches = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${GATEWAY_API}/container/branches`);
      const data = await res.json();
      setBranches(data.branches || []);
    } catch {
      setBranches([]);
    }
    setLoading(false);
  }, []);

  // Fetch status on mount and when signals change
  useEffect(() => { fetchStatus(); }, [fetchStatus, branchChangedSignal, turnCompleted]);

  // Fetch branches when expanded
  useEffect(() => {
    if (expanded) fetchBranches();
  }, [expanded, fetchBranches]);

  // Close on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setExpanded(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const switchBranch = async (branch: string) => {
    if (!status || status.active_turns > 0) return;
    setSwitching(true);
    setError(null);
    try {
      const res = await fetch(`${GATEWAY_API}/container/branch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch }),
      });
      const data = await res.json();
      if (!data.ok) {
        setError(data.error || "Switch failed");
      } else if (data.deferred) {
        setError("Switch deferred — will apply after current turn completes");
      } else {
        setExpanded(false);
        await fetchStatus();
      }
    } catch (e) {
      setError("Network error");
    }
    setSwitching(false);
  };

  const disabled = (status?.active_turns ?? 0) > 0;
  const currentBranch = status?.branch || "...";

  return (
    <div ref={ref} style={{ position: "relative", display: "inline-block" }}>
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          background: "none",
          border: "1px solid #2a2a3e",
          borderRadius: "6px",
          padding: "4px 10px",
          color: "#8888a0",
          fontSize: "0.78rem",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: "5px",
          opacity: disabled ? 0.5 : 1,
        }}
        title={disabled ? "Branch switching unavailable — a conversation is in progress" : "Switch branch"}
      >
        <span style={{ fontSize: "0.85rem" }}>&#x1F500;</span>
        <span style={{ color: "#e0e0e8", fontFamily: "monospace", fontSize: "0.78rem" }}>{currentBranch}</span>
        {status?.pending_reload && (
          <span style={{ color: "#ffa06c", fontSize: "0.65rem" }} title="Reload pending">&#x23F3;</span>
        )}
        <span style={{ fontSize: "0.6rem", color: "#5a5a6e" }}>{expanded ? "\u25B2" : "\u25BC"}</span>
      </button>
      {status?.container_id && status.container_id !== "default" && (
        <div style={{ fontSize: "0.6rem", color: "#5a5a6e", textAlign: "center", marginTop: "2px" }}>
          container: {status.container_id}
        </div>
      )}

      {expanded && (
        <div style={{
          position: "absolute",
          top: "calc(100% + 4px)",
          left: 0,
          minWidth: "220px",
          maxHeight: "300px",
          overflowY: "auto",
          backgroundColor: "#1e1e2e",
          border: "1px solid #2a2a3e",
          borderRadius: "10px",
          zIndex: 200,
          boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
        }}>
          {disabled && (
            <div style={{
              padding: "8px 12px",
              fontSize: "0.72rem",
              color: "#ffa06c",
              borderBottom: "1px solid #2a2a3e",
            }}>
              Another conversation is in progress — branch switching unavailable until it completes.
            </div>
          )}

          {error && (
            <div style={{
              padding: "8px 12px",
              fontSize: "0.72rem",
              color: "#ff6c8a",
              borderBottom: "1px solid #2a2a3e",
            }}>
              {error}
            </div>
          )}

          {loading ? (
            <div style={{ padding: "12px", color: "#8888a0", fontSize: "0.8rem", textAlign: "center" }}>
              Loading branches...
            </div>
          ) : (
            branches.map((b) => (
              <div
                key={b.name}
                onClick={() => !disabled && !switching && b.name !== currentBranch && switchBranch(b.name)}
                style={{
                  padding: "8px 12px",
                  cursor: disabled || switching || b.name === currentBranch ? "default" : "pointer",
                  fontSize: "0.82rem",
                  fontFamily: "monospace",
                  color: b.name === currentBranch ? "#6c8aff" : disabled ? "#5a5a6e" : "#e0e0e8",
                  backgroundColor: b.name === currentBranch ? "#12121a" : "transparent",
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  transition: "background-color 0.15s",
                }}
                onMouseEnter={(e) => {
                  if (b.name !== currentBranch && !disabled) {
                    (e.currentTarget as HTMLElement).style.backgroundColor = "#2a2a3e";
                  }
                }}
                onMouseLeave={(e) => {
                  if (b.name !== currentBranch) {
                    (e.currentTarget as HTMLElement).style.backgroundColor = "transparent";
                  }
                }}
              >
                <span>{b.name}</span>
                {b.name === currentBranch && (
                  <span style={{ fontSize: "0.65rem", color: "#6c8aff" }}>current</span>
                )}
              </div>
            ))
          )}

          {!loading && branches.length === 0 && (
            <div style={{ padding: "12px", color: "#5a5a6e", fontSize: "0.8rem", textAlign: "center" }}>
              No branches found
            </div>
          )}
        </div>
      )}
    </div>
  );
}
