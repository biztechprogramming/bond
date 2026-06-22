"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { GATEWAY_API, BACKEND_API, apiFetch } from "@/lib/config";

interface BranchInfo {
  name: string;
  lastCommit: string;
}

interface AgentRepo {
  id: string;
  name: string;
  default_branch: string;
  active_branch: string;   // user-desired target (what reconcile will check out)
  observed_branch: string; // heartbeat-observed actual branch
}

interface BranchStatus {
  container_id: string;
  branch: string;
  worker_online: boolean;
  worker_branch: string | null;
  active_turns: number | null;
  pending_reload: boolean;
  head_sha?: string | null;
  dev_mounted?: boolean;
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
  const [pulling, setPulling] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [reloading, setReloading] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [pendingBranch, setPendingBranch] = useState<string | null>(null);
  // Per-repo workspace branch state (Design Doc 120).
  const [repos, setRepos] = useState<AgentRepo[]>([]);
  const [repoBranches, setRepoBranches] = useState<Record<string, BranchInfo[]>>({});
  const [openRepoId, setOpenRepoId] = useState<string | null>(null);
  const [repoSwitching, setRepoSwitching] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const branchRef = useRef<HTMLDivElement>(null);

  // Prefer the worker's view of branch + head sha when we know the agent_id
  // (Design Doc 116 §3.2: worker is the source of truth). Falls back to the
  // gateway's container-branch endpoint for non-container or pre-spawn cases.
  const fetchStatus = useCallback(async () => {
    try {
      if (agentId) {
        const resp = await apiFetch(`${BACKEND_API}/agents/${encodeURIComponent(agentId)}/branch`);
        if (resp.ok) {
          const data = await resp.json();
          setStatus({
            container_id: data.container_id || agentId,
            branch: data.branch || "main",
            worker_online: !!data.online,
            worker_branch: data.branch,
            active_turns: data.active_turns,
            pending_reload: !!data.pending_reload,
            head_sha: data.head_sha || null,
            dev_mounted: !!data.dev_mounted,
          });
          if (pendingBranch && !data.pending_reload && data.branch === pendingBranch) {
            setPendingBranch(null);
          }
          return;
        }
      }
      // Fallback path
      const params = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
      const resp = await apiFetch(`${GATEWAY_API}/container/branch${params}`);
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
      const resp = await apiFetch(`${GATEWAY_API}/container/branches`);
      if (resp.ok) {
        const data = await resp.json();
        setBranches(data.branches || []);
      }
    } catch { /* ignore */ }
  }, []);

  // ── Workspace repos (Design Doc 120) ───────────────────────────────────
  // Each agent_repo carries a user-desired active_branch and a heartbeat-
  // observed observed_branch. The list lives in SpacetimeDB; we read it via the
  // backend REST endpoint and refresh on turn boundaries (when reconcile runs).
  const fetchRepos = useCallback(async () => {
    if (!agentId) { setRepos([]); return; }
    try {
      const resp = await apiFetch(`${BACKEND_API}/agents/${encodeURIComponent(agentId)}/repos`);
      if (resp.ok) setRepos(await resp.json());
    } catch { /* ignore */ }
  }, [agentId]);

  // Lazily fetch one repo's branch list when its picker opens (proxies to the
  // worker, which is where the repo is mounted).
  const fetchRepoBranches = useCallback(async (repoId: string) => {
    if (!agentId) return;
    try {
      const resp = await apiFetch(
        `${BACKEND_API}/agents/${encodeURIComponent(agentId)}/repos/${encodeURIComponent(repoId)}/branches`,
      );
      if (resp.ok) {
        const data = await resp.json();
        const list = (data.branches || []) as Array<{ name: string; lastCommit: string }>;
        setRepoBranches((m) => ({ ...m, [repoId]: list.map((b) => ({ name: b.name, lastCommit: b.lastCommit })) }));
      }
    } catch { /* ignore */ }
  }, [agentId]);

  // Set a repo's desired branch. The worker's turn-boundary reconcile does the
  // actual checkout (or asks the user in chat if the tree is dirty); we just
  // record the intent here, so this never touches the working tree directly.
  const switchRepoBranch = useCallback(async (repoId: string, branch: string) => {
    if (!agentId || repoSwitching) return;
    setRepoSwitching(repoId);
    setActionError(null);
    try {
      const resp = await apiFetch(
        `${BACKEND_API}/agents/${encodeURIComponent(agentId)}/repos/${encodeURIComponent(repoId)}`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ active_branch: branch }),
        },
      );
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setActionError(data.detail || `Set branch failed (${resp.status})`);
      } else {
        await fetchRepos();
      }
    } catch (e) {
      setActionError(`Set branch failed: ${(e as Error).message}`);
    }
    setRepoSwitching(null);
    setOpenRepoId(null);
  }, [agentId, repoSwitching, fetchRepos]);

  // Pull the worker's /bond to latest of its current branch (Design Doc 116 §3.7).
  const doPull = useCallback(async () => {
    if (!agentId || pulling) return;
    setPulling(true);
    setActionError(null);
    try {
      const resp = await apiFetch(`${BACKEND_API}/agents/${encodeURIComponent(agentId)}/pull`, {
        method: "POST",
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setActionError(data.detail || `Pull failed (${resp.status})`);
      }
      // Restart is in flight; status refresh will reflect the new sha once /branch responds
      await fetchStatus();
    } catch (e) {
      setActionError(`Pull failed: ${(e as Error).message}`);
    }
    setPulling(false);
  }, [agentId, pulling, fetchStatus]);

  // Fetch (no checkout, no restart) — refreshes the local branch list so
  // newly pushed branches appear in the dropdown without interrupting the agent.
  const doFetch = useCallback(async () => {
    if (!agentId || fetching) return;
    setFetching(true);
    setActionError(null);
    try {
      const resp = await apiFetch(`${BACKEND_API}/agents/${encodeURIComponent(agentId)}/fetch`, {
        method: "POST",
      });
      if (resp.ok) {
        const data = await resp.json();
        const list = (data.branches || []) as Array<{ name: string; sha: string; lastCommit: string }>;
        setBranches(list.map((b) => ({ name: b.name, lastCommit: b.lastCommit })));
      } else {
        // Fall back to gateway-side list
        await fetchBranches();
        const data = await resp.json().catch(() => ({}));
        setActionError(data.detail || `Fetch failed (${resp.status})`);
      }
    } catch (e) {
      setActionError(`Fetch failed: ${(e as Error).message}`);
    }
    setFetching(false);
  }, [agentId, fetching, fetchBranches]);

  // Dev-mounted only: reload the worker in place to pick up live host-tree
  // edits (no git). The single safe code refresh when /bond is the developer's
  // working tree — Pull/Branch-switch would mutate it.
  const doReload = useCallback(async () => {
    if (!agentId || reloading) return;
    setReloading(true);
    setActionError(null);
    try {
      const resp = await apiFetch(`${BACKEND_API}/agents/${encodeURIComponent(agentId)}/reload`, {
        method: "POST",
      });
      if (!resp.ok) {
        const data = await resp.json().catch(() => ({}));
        setActionError(data.detail || `Reload failed (${resp.status})`);
      }
      await fetchStatus();
    } catch (e) {
      setActionError(`Reload failed: ${(e as Error).message}`);
    }
    setReloading(false);
  }, [agentId, reloading, fetchStatus]);

  useEffect(() => { fetchStatus(); }, [fetchStatus, branchChangedSignal, turnCompleted]);

  useEffect(() => {
    if (branchDropdownOpen) fetchBranches();
  }, [branchDropdownOpen, fetchBranches]);

  // Refresh the repo list when the panel opens and after each turn (reconcile
  // runs at turn boundaries, so observed_branch may have just changed).
  useEffect(() => {
    if (expanded) fetchRepos();
  }, [expanded, fetchRepos, turnCompleted, branchChangedSignal]);

  useEffect(() => {
    if (openRepoId) fetchRepoBranches(openRepoId);
  }, [openRepoId, fetchRepoBranches]);

  // Close panel on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        setExpanded(false);
        setBranchDropdownOpen(false);
        setOpenRepoId(null);
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
    setActionError(null);
    try {
      // Prefer the worker /checkout path when we have an agent_id (Design Doc 116 §3.7):
      // no fetch, instant local checkout, in-place worker restart.
      if (agentId) {
        const resp = await apiFetch(
          `${BACKEND_API}/agents/${encodeURIComponent(agentId)}/checkout`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ branch }),
          },
        );
        if (resp.ok) {
          await fetchStatus();
        } else {
          const data = await resp.json().catch(() => ({}));
          if (resp.status === 409) {
            // Worker busy — fall back to deferred-reload path so the switch happens after the turn
            const fallback = await apiFetch(`${GATEWAY_API}/container/branch`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ branch, agent_id: agentId }),
            });
            if (fallback.ok) {
              const fb = await fallback.json();
              if (fb.deferred) setPendingBranch(branch);
              await fetchStatus();
            }
          } else {
            setActionError(data.detail || `Checkout failed (${resp.status})`);
          }
        }
      } else {
        const resp = await apiFetch(`${GATEWAY_API}/container/branch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ branch }),
        });
        if (resp.ok) {
          const data = await resp.json();
          if (data.deferred) setPendingBranch(branch);
          await fetchStatus();
        }
      }
    } catch (e) {
      setActionError(`Checkout failed: ${(e as Error).message}`);
    }
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
            <div style={labelStyle}>{status?.dev_mounted ? "Code" : "Branch"}</div>

            {/* Dev-mounted mode: /bond is the developer's live host tree. Git-based
                Pull / Branch-switch are unsafe (they'd mutate or reset it), so the
                only control is an in-place Reload that re-execs the worker. */}
            {status?.dev_mounted && (
              <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                <span
                  title="/bond is bind-mounted from your host working tree; edits are live"
                  style={{ ...valueStyle, fontFamily: "monospace", fontSize: "0.78rem", color: "#6c8aff", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                >
                  📁 dev-mounted (host tree)
                </span>
                <button
                  onClick={doReload}
                  disabled={!agentId || reloading || (activeTurns ?? 0) > 0}
                  title={
                    !agentId ? "No agent context"
                      : (activeTurns ?? 0) > 0 ? "Agent is working — wait for the turn to finish"
                      : reloading ? "Reloading…"
                      : "Reload worker code in place — picks up your local edits, no git"
                  }
                  style={iconButtonStyle(!agentId || reloading || (activeTurns ?? 0) > 0)}
                >
                  {reloading ? "…" : "↻"}
                </button>
              </div>
            )}

            {!status?.dev_mounted && (
            <div ref={branchRef} style={{ position: "relative", display: "flex", gap: "6px", alignItems: "stretch" }}>
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
                  flex: 1,
                  minWidth: 0,
                }}
              >
                <span style={{ fontSize: "0.8rem" }}>🔀</span>
                <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{currentBranch}</span>
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

              {/* Pull button — refreshes /bond to latest of current branch and self-restarts the worker */}
              <button
                onClick={doPull}
                disabled={!agentId || pulling || workerOffline || (activeTurns ?? 0) > 0}
                title={
                  !agentId ? "No agent context"
                    : workerOffline ? "Agent is offline"
                    : (activeTurns ?? 0) > 0 ? "Agent is working — wait for the turn to finish"
                    : pulling ? "Pulling…"
                    : `Pull latest of ${currentBranch} and reload`
                }
                style={iconButtonStyle(!agentId || pulling || workerOffline || (activeTurns ?? 0) > 0)}
              >
                {pulling ? "…" : "⤓"}
              </button>

              {/* Fetch button — refreshes the local branch list (always safe, even mid-turn) */}
              <button
                onClick={doFetch}
                disabled={!agentId || fetching || workerOffline}
                title={
                  !agentId ? "No agent context"
                    : workerOffline ? "Agent is offline"
                    : fetching ? "Fetching…"
                    : "Fetch from origin (refresh branch list)"
                }
                style={iconButtonStyle(!agentId || fetching || workerOffline)}
              >
                {fetching ? "…" : "⟳"}
              </button>
            </div>
            )}

            {actionError && (
              <div style={{ marginTop: "6px", fontSize: "0.7rem", color: "#ff6c8a" }}>{actionError}</div>
            )}
            {status?.head_sha && (
              <div style={{ marginTop: "6px", fontSize: "0.68rem", color: "#5a5a6e", fontFamily: "monospace" }}>
                {status.head_sha.slice(0, 12)}
              </div>
            )}

            {/* Dropdown — anchored to branch button container */}
            <div style={{ position: "relative" }}>
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

          {/* Section: Workspace repos (Design Doc 120) — per-repo branch view + picker */}
          {repos.length > 0 && (
            <div style={sectionStyle}>
              <div style={labelStyle}>Workspace repos</div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {repos.map((repo) => {
                  // "unknown" is the schema sentinel default (see Doc 120 §5.1);
                  // treat it and "" as "not yet observed".
                  const observedRaw = repo.observed_branch === "unknown" ? "" : repo.observed_branch;
                  const observed = observedRaw || "—";
                  const desired = (repo.active_branch || "").trim();
                  // Desired differs from observed → a switch is queued for the next
                  // turn (or blocked by uncommitted work, in which case the agent
                  // asks in chat). We can't yet tell those apart here, so label it
                  // honestly as pending.
                  const pending =
                    !!desired &&
                    !!observedRaw &&
                    !observedRaw.startsWith("detached:") &&
                    desired !== observedRaw;
                  const isOpen = openRepoId === repo.id;
                  const list = repoBranches[repo.id] || [];
                  return (
                    <div key={repo.id} style={{ position: "relative" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                        <span
                          title={repo.name}
                          style={{
                            fontSize: "0.74rem",
                            color: "#8888a0",
                            minWidth: "64px",
                            maxWidth: "96px",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                        >
                          {repo.name}
                        </span>
                        <button
                          onClick={() => setOpenRepoId(isOpen ? null : repo.id)}
                          disabled={!agentId || repoSwitching === repo.id}
                          style={{
                            backgroundColor: "#1e1e2e",
                            borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
                            borderRadius: "6px",
                            padding: "4px 10px",
                            color: "#e0e0e8",
                            fontSize: "0.78rem",
                            cursor: !agentId ? "not-allowed" : "pointer",
                            display: "flex",
                            alignItems: "center",
                            gap: "6px",
                            fontFamily: "monospace",
                            flex: 1,
                            minWidth: 0,
                            opacity: !agentId ? 0.55 : 1,
                          }}
                        >
                          <span style={{ fontSize: "0.78rem" }}>🔀</span>
                          <span style={{ flex: 1, textAlign: "left", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {observed}
                          </span>
                          {pending && (
                            <span
                              title={`Switching to ${desired} — applies on the next message, or the agent will ask if there are uncommitted changes`}
                              style={{ fontSize: "0.68rem", color: "#ffa06c", flexShrink: 0 }}
                            >
                              ⏳ {desired}
                            </span>
                          )}
                          {repoSwitching === repo.id && (
                            <span style={{ fontSize: "0.68rem", color: "#ffa06c" }}>…</span>
                          )}
                          <span style={{ fontSize: "0.6rem", color: "#5a5a6e", flexShrink: 0 }}>{isOpen ? "▲" : "▼"}</span>
                        </button>
                      </div>

                      {isOpen && (
                        <div
                          style={{
                            position: "absolute",
                            top: "calc(100% + 4px)",
                            left: 0,
                            right: 0,
                            minWidth: "180px",
                            backgroundColor: "#1e1e2e",
                            borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
                            borderRadius: "8px",
                            overflow: "hidden",
                            zIndex: 160,
                            boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
                            maxHeight: "240px",
                            overflowY: "auto",
                          }}
                        >
                          {list.length === 0 && (
                            <div style={{ padding: "10px 14px", color: "#5a5a6e", fontSize: "0.76rem" }}>
                              Loading…
                            </div>
                          )}
                          {list.map((b) => {
                            const isCurrent = b.name === observedRaw;
                            const isDesired = b.name === desired;
                            return (
                              <div
                                key={b.name}
                                onClick={() => !isDesired && switchRepoBranch(repo.id, b.name)}
                                style={{
                                  padding: "8px 14px",
                                  cursor: isDesired ? "default" : "pointer",
                                  fontSize: "0.78rem",
                                  fontFamily: "monospace",
                                  color: isCurrent ? "#6c8aff" : "#e0e0e8",
                                  backgroundColor: isCurrent ? "#12121a" : "transparent",
                                  display: "flex",
                                  justifyContent: "space-between",
                                  alignItems: "center",
                                  gap: "8px",
                                }}
                                onMouseEnter={(e) => {
                                  if (!isDesired) (e.currentTarget as HTMLElement).style.backgroundColor = "#2a2a3e";
                                }}
                                onMouseLeave={(e) => {
                                  if (!isDesired) (e.currentTarget as HTMLElement).style.backgroundColor = isCurrent ? "#12121a" : "transparent";
                                }}
                              >
                                <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{b.name}</span>
                                {isCurrent && <span style={{ color: "#6c8aff", fontSize: "0.66rem", flexShrink: 0 }}>current</span>}
                                {!isCurrent && isDesired && <span style={{ color: "#ffa06c", fontSize: "0.66rem", flexShrink: 0 }}>queued</span>}
                              </div>
                            );
                          })}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

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

function iconButtonStyle(disabled: boolean): React.CSSProperties {
  return {
    backgroundColor: "#1e1e2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "6px",
    padding: "4px 9px",
    color: disabled ? "#5a5a6e" : "#e0e0e8",
    fontSize: "0.95rem",
    cursor: disabled ? "not-allowed" : "pointer",
    fontFamily: "monospace",
    flexShrink: 0,
    opacity: disabled ? 0.55 : 1,
    minWidth: "30px",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  };
}

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
