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

interface ConversationInfoPanelProps {
  branchChangedSignal: number;
  turnCompleted: number;
  agentId?: string | null;
  agentName: string;
  connectionState: string;
  agentStatus: string;
  conversationId: string | null;
}

export default function ConversationInfoPanel({
  branchChangedSignal,
  turnCompleted,
  agentId,
  agentName,
  connectionState,
  agentStatus,
  conversationId,
}: ConversationInfoPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [branchDropdownOpen, setBranchDropdownOpen] = useState(false);
  const [branches, setBranches] = useState<BranchInfo[]>([]);
  const [status, setStatus] = useState<BranchStatus | null>(null);
  const [switching, setSwitching] = useState(false);
  const [pendingBranch, setPendingBranch] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const branchRef = useRef<HTMLDivElement>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const params = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
      const resp = await fetch(`${GATEWAY_API}/container/branch${params}`);
      if (resp.ok) {
        const data = await resp.json();
        setStatus(data);
        if (pendingBranch && !data.pending_reload && data.branch === pendingBranch) {
          setPendingBranch(null);
        }
      }
    } catch { /* ignore */ }
  }, [agentId, pendingBranch]);

  const fetchBranches = useCallback(async () => {
    try {
      const resp = await fetch(`${GATEWAY_API}/container/branches`);
      if (resp.ok) {
        const data = await resp.json();
        setBranches(data.branches || []);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchStatus(); }, [fetchStatus, branchChangedSignal, turnCompleted]);

  useEffect(() => {
    if (branchDropdownOpen) fetchBranches();
  }, [branchDropdownOpen, fetchBranches]);

  // Close panel on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setExpanded(false);
        setBranchDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Close branch dropdown on outside click within panel
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (branchRef.current && !branchRef.current.contains(e.target as Node)) {
        setBranchDropdownOpen(false);
      }
    };
    if (branchDropdownOpen) {
      document.addEventListener("mousedown", handler);
      return () => document.removeEventListener("mousedown", handler);
    }
  }, [branchDropdownOpen]);

  const switchBranch = async (branch: string) => {
    if (switching) return;
    setSwitching(true);
    try {
      const resp = await fetch(`${GATEWAY_API}/container/branch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ branch, ...(agentId ? { agent_id: agentId } : {}) }),
      });
      if (resp.ok) {
        const data = await resp.json();
        if (data.deferred) {
          setPendingBranch(branch);
        }
        await fetchStatus();
      }
    } catch { /* ignore */ }
    setSwitching(false);
    setBranchDropdownOpen(false);
  };

  const currentBranch = pendingBranch || status?.branch || "main";
  const workerOffline = status !== null && !status.worker_online;
  const activeTurns = status?.active_turns ?? 0;

  const statusColor = connectionState === "connected" ? "#4ec994"
    : connectionState === "reconnecting" ? "#ffa06c"
    : "#ff6c8a";

  const agentStatusLabel = agentStatus === "idle" ? null
    : agentStatus === "thinking" ? "Thinking…"
    : agentStatus === "tool_calling" ? "Using tools…"
    : agentStatus === "responding" ? "Responding…"
    : agentStatus === "stopping" ? "Stopping…"
    : agentStatus;

  return (
    <div ref={panelRef} style={{ position: "relative" }}>
      {/* Toggle button — small info icon */}
      <button
        onClick={() => setExpanded(!expanded)}
        title="Conversation info"
        style={{
          background: expanded ? "#1e1e2e" : "none",
          border: expanded ? "1px solid #2a2a3e" : "1px solid transparent",
          borderRadius: "6px",
          padding: "4px 8px",
          color: expanded ? "#e0e0e8" : "#5a5a6e",
          fontSize: "0.85rem",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          gap: "5px",
          transition: "all 0.15s ease",
        }}
      >
        <span style={{ fontSize: "0.8rem" }}>ℹ️</span>
        <span style={{
          width: "6px",
          height: "6px",
          borderRadius: "50%",
          backgroundColor: statusColor,
          display: "inline-block",
          flexShrink: 0,
        }} />
        {(status?.pending_reload || pendingBranch) && (
          <span style={{ fontSize: "0.7rem", color: "#ffa06c" }}>⏳</span>
        )}
      </button>

      {/* Expandable panel */}
      {expanded && (
        <div
          style={{
            position: "absolute",
            top: "calc(100% + 6px)",
            left: 0,
            minWidth: "300px",
            backgroundColor: "#12121a",
            borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
            borderRadius: "12px",
            overflow: "visible",
            zIndex: 150,
            boxShadow: "0 12px 32px rgba(0,0,0,0.5)",
            padding: "16px",
          }}
        >
          {/* Section: Connection */}
          <div style={sectionStyle}>
            <div style={labelStyle}>Connection</div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{
                width: "8px",
                height: "8px",
                borderRadius: "50%",
                backgroundColor: statusColor,
                display: "inline-block",
                flexShrink: 0,
              }} />
              <span style={valueStyle}>
                {connectionState === "connected" ? "Connected"
                  : connectionState === "reconnecting" ? "Reconnecting…"
                  : connectionState === "connecting" ? "Connecting…"
                  : "Disconnected"}
              </span>
            </div>
          </div>

          {/* Section: Agent Status */}
          {agentStatusLabel && (
            <div style={sectionStyle}>
              <div style={labelStyle}>Agent</div>
              <span style={{ ...valueStyle, color: "#ffa06c" }}>{agentStatusLabel}</span>
            </div>
          )}

          {/* Section: Branch */}
          <div style={sectionStyle}>
            <div style={labelStyle}>Branch</div>
            <div ref={branchRef} style={{ position: "relative" }}>
              <button
                onClick={() => setBranchDropdownOpen(!branchDropdownOpen)}
                style={{
                  backgroundColor: "#1e1e2e",
                  borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
                  borderRadius: "6px",
                  padding: "4px 10px",
                  color: "#e0e0e8",
                  fontSize: "0.8rem",
                  cursor: "pointer",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  fontFamily: "monospace",
                  width: "100%",
                }}
              >
                <span style={{ fontSize: "0.8rem" }}>🔀</span>
                <span style={{ flex: 1, textAlign: "left" }}>{currentBranch}</span>
                {workerOffline && (
                  <span
                    title="Worker offline"
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
                  <span title={`Switching to ${pendingBranch || "new branch"}`} style={{ fontSize: "0.7rem", color: "#ffa06c" }}>⏳</span>
                )}
                <span style={{ fontSize: "0.65rem", color: "#5a5a6e" }}>{branchDropdownOpen ? "▲" : "▼"}</span>
              </button>

              {branchDropdownOpen && (
                <div
                  style={{
                    position: "absolute",
                    top: "calc(100% + 4px)",
                    left: 0,
                    right: 0,
                    minWidth: "200px",
                    backgroundColor: "#1e1e2e",
                    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
                    borderRadius: "8px",
                    overflow: "hidden",
                    zIndex: 160,
                    boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
                  }}
                >
                  {branches.length === 0 && (
                    <div style={{ padding: "10px 14px", color: "#5a5a6e", fontSize: "0.78rem" }}>
                      Loading…
                    </div>
                  )}
                  {branches.map((b) => (
                    <div
                      key={b.name}
                      onClick={() => b.name !== currentBranch && switchBranch(b.name)}
                      style={{
                        padding: "8px 14px",
                        cursor: b.name === currentBranch ? "default" : "pointer",
                        fontSize: "0.8rem",
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
                        <span style={{ color: "#6c8aff", fontSize: "0.68rem" }}>current</span>
                      )}
                    </div>
                  ))}
                  {workerOffline && (
                    <div style={{
                      padding: "6px 14px",
                      borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#2a2a3e",
                      color: "#8888a0",
                      fontSize: "0.7rem",
                    }}>
                      Container offline — will start on selected branch
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

          {/* Section: Worker */}
          <div style={sectionStyle}>
            <div style={labelStyle}>Worker</div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <span style={{
                width: "6px",
                height: "6px",
                borderRadius: "50%",
                backgroundColor: workerOffline ? "#ff6c8a" : "#4ec994",
                display: "inline-block",
                flexShrink: 0,
              }} />
              <span style={valueStyle}>
                {workerOffline ? "Offline" : "Online"}
              </span>
              {activeTurns > 0 && (
                <span style={{ ...valueStyle, color: "#ffa06c", fontSize: "0.72rem" }}>
                  ({activeTurns} active turn{activeTurns !== 1 ? "s" : ""})
                </span>
              )}
            </div>
          </div>

          {/* Section: Container */}
          {status?.container_id && status.container_id !== "default" && (
            <div style={sectionStyle}>
              <div style={labelStyle}>Container</div>
              <span style={{ ...valueStyle, fontFamily: "monospace", fontSize: "0.72rem" }}>
                {status.container_id}
              </span>
            </div>
          )}

          {/* Section: Conversation */}
          {conversationId && (
            <div style={{ ...sectionStyle, borderBottomWidth: 0, borderBottomStyle: "none", borderBottomColor: "transparent", paddingBottom: 0 }}>
              <div style={labelStyle}>Conversation</div>
              <span style={{ ...valueStyle, fontFamily: "monospace", fontSize: "0.68rem", color: "#5a5a6e" }}>
                {conversationId.length > 16 ? conversationId.slice(0, 8) + "…" + conversationId.slice(-8) : conversationId}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const sectionStyle: React.CSSProperties = {
  paddingBottom: "10px",
  marginBottom: "10px",
  borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
};

const labelStyle: React.CSSProperties = {
  fontSize: "0.68rem",
  color: "#5a5a6e",
  textTransform: "uppercase",
  letterSpacing: "0.05em",
  marginBottom: "4px",
  fontWeight: 600,
};

const valueStyle: React.CSSProperties = {
  fontSize: "0.82rem",
  color: "#e0e0e8",
};
