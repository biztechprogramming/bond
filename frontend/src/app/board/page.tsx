"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { GatewayWebSocket, type GatewayMessage } from "@/lib/ws";
import type { ChatMessage, AgentStatus } from "@/lib/types";
import { STATUS_EMOJI, KANBAN_COLUMNS } from "@/lib/theme";
import ChatPanel from "@/components/shared/ChatPanel";
import PlanSelector from "@/components/shared/PlanSelector";
import KanbanColumn from "@/components/shared/KanbanColumn";
import { useSpacetimeDB, useWorkPlans, useWorkItems } from "@/hooks/useSpacetimeDB";
import { 
  connectToSpacetimeDB, 
  getWorkPlans, 
  getWorkItems,
  getConversations,
  type WorkPlan as STDBWorkPlan,
  type WorkItem as STDBWorkItem
} from "@/lib/spacetimedb-client";

const API_BASE = "http://localhost:18790/api/v1";

// -- Board Page --

export default function BoardPageWrapper() {
  return (
    <Suspense>
      <BoardPage />
    </Suspense>
  );
}

function BoardPage() {
  const searchParams = useSearchParams();
  
  // ── SpacetimeDB Reactive State ──
  const stdbPlans = useWorkPlans();
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    // URL param wins, then localStorage, then null (auto-select picks it up)
    return searchParams.get("plan") || localStorage.getItem("bond-selected-plan-id");
  });

  // Track whether the user has explicitly chosen a plan this session
  const userPickedPlanRef = useRef(false);
  
  const currentPlanItems = useWorkItems(selectedPlanId || "");

  // Derive selected plan with items for compatibility
  const selectedPlan = useSpacetimeDB(() => {
    if (!selectedPlanId) return null;
    const plans = getWorkPlans();
    const plan = plans.find(p => p.id === selectedPlanId);
    if (!plan) return null;
    
    const items = getWorkItems(plan.id).map(it => {
        let notes: any[] = [];
        let files_changed: string[] = [];
        try { notes = JSON.parse(it.notes || "[]"); } catch { notes = []; }
        try { files_changed = JSON.parse(it.filesChanged || "[]"); } catch { files_changed = []; }

        return {
            ...it,
            notes,
            files_changed,
            created_at: new Date(Number(it.createdAt)).toISOString(),
            updated_at: new Date(Number(it.updatedAt)).toISOString(),
        };
    });

    return { 
        ...plan, 
        items,
        created_at: new Date(Number(plan.createdAt)).toISOString(),
        updated_at: new Date(Number(plan.updatedAt)).toISOString(),
    } as any;
  }, [selectedPlanId]);

  const [expandedItemId, setExpandedItemId] = useState<string | null>(null);
  const [dragItemId, setDragItemId] = useState<string | null>(null);
  const [dragOverColumn, setDragOverColumn] = useState<string | null>(null);
  const [lineage, setLineage] = useState<{
    parents: { id: string; title: string; status: string }[];
    current: { id: string; title: string; status: string } | null;
    children: { id: string; title: string; status: string }[];
  } | null>(null);
  const [showLineage, setShowLineage] = useState(false);

  // Chat state
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>("idle");
  const [streamingContent, setStreamingContent] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("bond-conversation-id");
    }
    return null;
  });
  const [agents, setAgents] = useState<{ id: string; display_name: string; is_default: boolean }[]>([]);
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const currentAgentNameRef = useRef<string>("Agent");
  const selectedAgentName = agents.find(a => a.id === selectedAgentId)?.display_name || currentAgentNameRef.current;
  const [toolActivity, setToolActivity] = useState<{ name: string; args: string; time: number }[]>([]);

  const wsRef = useRef<GatewayWebSocket | null>(null);
  const selectedPlanIdRef = useRef<string | null>(selectedPlanId);
  useEffect(() => { selectedPlanIdRef.current = selectedPlanId; }, [selectedPlanId]);

  // Keep currentAgentNameRef in sync with selected agent
  useEffect(() => {
    const name = agents.find(a => a.id === selectedAgentId)?.display_name;
    if (name) currentAgentNameRef.current = name;
  }, [selectedAgentId, agents]);

  // Initialize SpacetimeDB connection
  useEffect(() => {
    connectToSpacetimeDB();
  }, []);

  // Persist conversation ID
  useEffect(() => {
    if (conversationId) {
      localStorage.setItem("bond-conversation-id", conversationId);
    }
  }, [conversationId]);

  // Fetch agents
  useEffect(() => {
    fetch(`${API_BASE}/agents`)
      .then(r => r.json())
      .then((data: { id: string; display_name: string; is_default: boolean }[]) => {
        setAgents(data);
        // Only fall back to default agent if the current plan doesn't own a specific agent
        const currentPlan = getWorkPlans().find(p => p.id === selectedPlanIdRef.current);
        const planAgentId = currentPlan?.agentId || (currentPlan as any)?.agent_id;
        const def = data.find(a => a.is_default);
        if (!planAgentId && def) setSelectedAgentId(def.id);
      })
      .catch(() => {});
  }, []);

  // Persist selected plan to localStorage
  useEffect(() => {
    if (selectedPlanId) {
      localStorage.setItem("bond-selected-plan-id", selectedPlanId);
    }
  }, [selectedPlanId]);

  // Auto-select initial plan only when nothing is selected yet
  useEffect(() => {
    if (!selectedPlanId && stdbPlans.length > 0) {
      const active = stdbPlans.find(p => p.status === "active") || stdbPlans[0];
      setSelectedPlanId(active.id);
    }
  }, [selectedPlanId, stdbPlans]);

  // When selected plan changes, switch the chat pane to its conversation and agent
  useEffect(() => {
    if (!selectedPlanId) return;
    const plans = getWorkPlans();
    const plan = plans.find(p => p.id === selectedPlanId);
    const convId = plan?.conversationId || (plan as any)?.conversation_id;
    if (!convId) return;

    setConversationId(convId);
    // Switch to the plan's agent so messages go to the correct worker
    const agentId = plan?.agentId || (plan as any)?.agent_id;
    if (agentId) {
      setSelectedAgentId(agentId);
    }
    setMessages([]);
    // Request history for this conversation from the gateway
    if (wsRef.current?.connected) {
      wsRef.current.switchConversation(convId);
    }
  }, [selectedPlanId]);

  // Fetch plan lineage (still uses API for now as lineage logic is complex for SQL-less SpacetimeDB)
  const fetchLineage = useCallback(async () => {
    if (!selectedPlanId) return;
    try {
      const res = await fetch(`${API_BASE}/plans/${selectedPlanId}/lineage`);
      if (res.ok) setLineage(await res.json());
    } catch { /* ignore */ }
  }, [selectedPlanId]);

  useEffect(() => { if (selectedPlanId) fetchLineage(); }, [selectedPlanId, fetchLineage]);

  // WebSocket for Chat
  useEffect(() => {
    let cancelled = false;
    const ws = new GatewayWebSocket();
    wsRef.current = ws;

    ws.onMessage((msg: GatewayMessage) => {
      if (cancelled) return;
      if (msg.type === "connected") {
        setConnected(true);
        // Use the plan's conversation if one is selected, otherwise fall back to localStorage
        const plans = getWorkPlans();
        const activePlanId = selectedPlanIdRef.current || (typeof window !== "undefined" ? searchParams.get("plan") : null);
        const activePlan = activePlanId ? plans.find(p => p.id === activePlanId) : null;
        const convId = activePlan?.conversationId || (activePlan as any)?.conversation_id
          || localStorage.getItem("bond-conversation-id");
        if (convId) ws.switchConversation(convId);
      } else if (msg.type === "status") {
        const status = msg.agentStatus || "idle";
        setAgentStatus(status);
        if (msg.agentName) currentAgentNameRef.current = msg.agentName;
        if (status !== "idle") {
          setLoading(true);
        }
      } else if (msg.type === "tool_call" && msg.content) {
        try {
          const data = JSON.parse(msg.content);
          const name = data.tool_name || data.name || "tool";
          const args = data.args ? (typeof data.args === "string" ? data.args : JSON.stringify(data.args)) : "";
          let summary = args.substring(0, 60);
          if (name === "work_plan") {
            const parsed = typeof data.args === "string" ? JSON.parse(data.args) : data.args;
            summary = `${parsed?.action || ""} ${parsed?.title || parsed?.status || ""}`.trim();
          }
          setToolActivity(prev => [...prev, { name, args: summary, time: Date.now() }]);
        } catch { /* ignore */ }
      } else if (msg.type === "chunk" && msg.content) {
        setStreamingContent(prev => prev + msg.content!);
        setAgentStatus("responding");
      } else if (msg.type === "done") {
        setStreamingContent(prev => {
          if (prev) {
            setMessages(msgs => {
              if (msg.messageId && msgs.some(m => m.id === msg.messageId)) return msgs;
              return [...msgs, { id: msg.messageId, role: "assistant", content: prev, agentName: msg.agentName || currentAgentNameRef.current }];
            });
          }
          return "";
        });
        setLoading(false);
        setAgentStatus("idle");
        setToolActivity([]);
        if (msg.conversationId) setConversationId(msg.conversationId);
      } else if (msg.type === "history" && msg.messages) {
        setMessages(
          msg.messages
            .filter(m => m.role === "user" || m.role === "assistant")
            .map(m => ({ id: m.id, role: m.role as "user" | "assistant", content: m.content }))
        );
        if (msg.conversationId) setConversationId(msg.conversationId);
      } else if (msg.type === "error") {
        setMessages(prev => [...prev, { role: "system", content: `Error: ${msg.error || "Unknown error"}` }]);
        setLoading(false);
        setAgentStatus("idle");
      }
      
      // Only auto-switch to a newly created plan if the user hasn't manually
      // chosen one. Once the user has picked, agent-created plans don't hijack.
      if (msg.type === "plan_created" && msg.planId && !userPickedPlanRef.current) {
        setSelectedPlanId(msg.planId);
      }
    });

    ws.connect();
    return () => { cancelled = true; ws.disconnect(); };
  }, []);

  // -- Actions --

  const sendMessage = useCallback(() => {
    if (!input.trim() || !wsRef.current?.connected) return;
    const content = input.trim();
    setInput("");
    setMessages(prev => [...prev, { role: "user", content }]);
    setLoading(true);
    wsRef.current.send(content, conversationId || undefined, selectedAgentId || undefined, selectedPlanId || undefined);
  }, [input, conversationId, selectedAgentId]);

  const handleStop = useCallback(() => {
    if (!wsRef.current?.connected || !conversationId) return;
    wsRef.current.interrupt(conversationId);
  }, [conversationId]);

  const handlePause = useCallback(async () => {
    if (!conversationId) return;
    wsRef.current?.pause(conversationId);
  }, [conversationId]);

  const handleResume = useCallback(async () => {
    if (!selectedPlanId) return;
    try {
      const res = await fetch(`${API_BASE}/plans/${selectedPlanId}/resume`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        if (data.recovery_context && wsRef.current?.connected) {
          wsRef.current.send(
            `Resume work plan: ${data.recovery_context}`,
            conversationId || undefined,
            selectedAgentId || undefined
          );
          setLoading(true);
        }
      }
    } catch { /* ignore */ }
  }, [selectedPlanId, conversationId, selectedAgentId]);

  const handleCancel = useCallback(async () => {
    if (!selectedPlanId) return;
    try {
      await fetch(`${API_BASE}/plans/${selectedPlanId}`, { method: "DELETE" });
      // Reactive reload happens automatically
    } catch { /* ignore */ }
    if (conversationId) wsRef.current?.interrupt(conversationId);
  }, [selectedPlanId, conversationId]);

  // -- Kanban helpers --

  const itemsByColumn = (column: string): STDBWorkItem[] => {
    if (!selectedPlan?.items) return [];
    return selectedPlan.items.filter((item: any) => item.status === column).sort((a: any, b: any) => a.ordinal - b.ordinal);
  };

  const planIsActive = selectedPlan?.status === "active" || selectedPlan?.status === "paused";

  const handleDragOver = (e: React.DragEvent, columnKey: string) => {
    e.preventDefault();
    setDragOverColumn(columnKey);
  };

  const handleDrop = async (columnKey: string) => {
    if (!dragItemId || !selectedPlanId) return;
    setDragOverColumn(null);
    setDragItemId(null);
    try {
      await fetch(`${API_BASE}/plans/${selectedPlanId}/items/${dragItemId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: columnKey }),
      });
      // SpacetimeDB sync is triggered by backend, frontend re-renders reactively
    } catch { /* ignore */ }
  };

  // -- Render --

  const showPauseFab = planIsActive && agentStatus !== "idle";
  const showResumeFab = selectedPlan?.status === "paused" && !showPauseFab;

  return (
    <div style={s.outerContainer}>
      {/* Header */}
      <header className="board-header" style={s.header}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <a href="/" style={s.chatToggle} title="Switch to Chat view">
            &#x1F4AC; Chat
          </a>
          <span style={s.boardToggleActive}>
            &#x1F4CB; Board
          </span>
          <a href="/board/plans" style={s.allPlansLink} title="Browse all plans">
            All Plans
          </a>
        </div>

        {/* Plan selector */}
        <PlanSelector
          plans={stdbPlans as any}
          selectedPlanId={selectedPlanId}
          selectedPlan={selectedPlan as any}
          onSelect={(id) => {
            userPickedPlanRef.current = true;
            setSelectedPlanId(id);
          }}
        />

        {/* Controls */}
        <div className="board-header-controls" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {planIsActive && agentStatus !== "idle" && (
            <button className="board-pause-btn-inline" onClick={handlePause} style={s.pauseBtn} title="Pause agent">
              &#x23F8; Pause
            </button>
          )}
          {selectedPlan?.status === "paused" && (
            <button onClick={handleResume} style={s.resumeBtn} title="Resume plan">
              &#x25B6; Resume
            </button>
          )}
          {planIsActive && (
            <button onClick={handleCancel} style={s.cancelBtn} title="Cancel plan">
              &#x23F9; Cancel
            </button>
          )}
          <span style={{ ...s.statusDot, color: connected ? "#6cffa0" : "#ff6c8a" }}>
            {connected ? "\u25CF" : "\u25CB"}
          </span>
        </div>
      </header>

      {/* Mobile FAB for pause/resume */}
      {(showPauseFab || showResumeFab) && (
        <button
          className="board-fab"
          onClick={showPauseFab ? handlePause : handleResume}
          style={s.fab}
          title={showPauseFab ? "Pause agent" : "Resume plan"}
        >
          {showPauseFab ? "\u23F8" : "\u25B6"}
        </button>
      )}

      {/* Main content: Kanban + Chat */}
      <div className="board-main-content" style={s.mainContent}>
        {/* Kanban Board */}
        <div className="board-kanban-area" style={s.kanbanArea}>
          {/* Plan Lineage Breadcrumb */}
          {selectedPlan && lineage && (lineage.parents.length > 0 || lineage.children.length > 0) && (
            <div style={s.lineageBar}>
              <button
                onClick={() => setShowLineage(!showLineage)}
                style={s.lineageToggle}
              >
                {showLineage ? "\u25BC" : "\u25B6"} Lineage
              </button>
              {showLineage && (
                <div style={s.lineageChain}>
                  {lineage.parents.map(p => (
                    <span key={p.id} style={s.lineageItem}>
                      <button
                        onClick={() => setSelectedPlanId(p.id)}
                        style={s.lineageLink}
                      >
                        {STATUS_EMOJI[p.status] || ""} {p.title}
                      </button>
                      <span style={{ color: "#3a3a4e", margin: "0 6px" }}>&rarr;</span>
                    </span>
                  ))}
                  <span style={{ ...s.lineageLink, color: "#6c8aff", fontWeight: 600, cursor: "default" }}>
                    {STATUS_EMOJI[lineage.current?.status || ""] || ""} {lineage.current?.title}
                  </span>
                  {lineage.children.length > 0 && (
                    <>
                      <span style={{ color: "#3a3a4e", margin: "0 6px" }}>&rarr;</span>
                      {lineage.children.map((c, i) => (
                        <span key={c.id} style={s.lineageItem}>
                          {i > 0 && <span style={{ color: "#3a3a4e", margin: "0 4px" }}>|</span>}
                          <button
                            onClick={() => setSelectedPlanId(c.id)}
                            style={s.lineageLink}
                          >
                            {STATUS_EMOJI[c.status] || ""} {c.title}
                          </button>
                        </span>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          )}

          {!selectedPlan ? (
            <div style={s.emptyBoard}>
              <div style={{ fontSize: "2rem", marginBottom: "12px" }}>&#x1F4CB;</div>
              <div>No plan selected</div>
              <div style={{ fontSize: "0.8rem", color: "#5a5a6e", marginTop: "8px" }}>
                Plans are created automatically when the agent starts a multi-step task.
                <br />Use the chat panel to send a task to the agent.
              </div>
            </div>
          ) : (
            <div className="board-columns-container" style={s.columnsContainer}>
              {KANBAN_COLUMNS.map(col => (
                <KanbanColumn
                  key={col.key}
                  columnKey={col.key}
                  label={col.label}
                  items={itemsByColumn(col.key) as any}
                  expandedItemId={expandedItemId}
                  dragItemId={dragItemId}
                  isDropTarget={dragOverColumn === col.key}
                  onToggleExpand={(itemId) => setExpandedItemId(expandedItemId === itemId ? null : itemId)}
                  onDragStart={(itemId) => setDragItemId(itemId)}
                  onDragEnd={() => { setDragItemId(null); setDragOverColumn(null); }}
                  onDragOver={(e) => handleDragOver(e, col.key)}
                  onDragLeave={() => setDragOverColumn(null)}
                  onDrop={() => handleDrop(col.key)}
                />
              ))}
            </div>
          )}
        </div>

        {/* Chat Panel */}
        <div className="board-chat-panel" style={s.chatPanel}>
          <div style={{ padding: "8px 12px 0", fontSize: "0.72rem", color: "#6c8aff", fontWeight: 600, letterSpacing: "0.05em", textTransform: "uppercase" as const }}>
            {selectedAgentName}
          </div>
          <ChatPanel
            messages={messages}
            input={input}
            onInputChange={setInput}
            onSend={sendMessage}
            onStop={handleStop}
            connected={connected}
            loading={loading}
            agentStatus={agentStatus}
            streamingContent={streamingContent}
            currentAgentName={currentAgentNameRef.current}
            selectedAgentName={selectedAgentName}
            toolActivity={toolActivity}
            compact={true}
            emptyMessage="Send a message to start a task."
            placeholder={loading ? "Type to queue..." : "Type a message..."}
          />
        </div>
      </div>
    </div>
  );
}

// -- Styles --

const s: Record<string, React.CSSProperties> = {
  outerContainer: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
  },
  header: {
    display: "flex",
    alignItems: "center",
    padding: "12px 20px",
    borderBottom: "1px solid #1e1e2e",
    gap: "8px",
    flexShrink: 0,
  },
  chatToggle: {
    color: "#8888a0",
    textDecoration: "none",
    fontSize: "0.85rem",
    padding: "6px 12px",
    borderRadius: "8px",
    border: "1px solid #2a2a3e",
    backgroundColor: "transparent",
  },
  boardToggleActive: {
    color: "#6c8aff",
    fontSize: "0.85rem",
    padding: "6px 12px",
    borderRadius: "8px",
    border: "1px solid #6c8aff",
    backgroundColor: "rgba(108,138,255,0.1)",
  },
  pauseBtn: {
    backgroundColor: "#ffcc44",
    color: "#000",
    border: "none",
    borderRadius: "8px",
    padding: "6px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  resumeBtn: {
    backgroundColor: "#6cffa0",
    color: "#000",
    border: "none",
    borderRadius: "8px",
    padding: "6px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  cancelBtn: {
    backgroundColor: "#ff4444",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "6px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  statusDot: {
    fontSize: "0.9rem",
    marginLeft: "4px",
  },
  allPlansLink: {
    color: "#5a5a6e",
    textDecoration: "none",
    fontSize: "0.8rem",
    padding: "4px 8px",
    borderRadius: "6px",
    border: "1px solid #2a2a3e",
  },
  fab: {
    display: "none",
    position: "fixed" as const,
    bottom: "24px",
    right: "24px",
    width: "56px",
    height: "56px",
    borderRadius: "50%",
    backgroundColor: "#ffcc44",
    color: "#000",
    border: "none",
    fontSize: "1.4rem",
    alignItems: "center",
    justifyContent: "center",
    cursor: "pointer",
    zIndex: 200,
    boxShadow: "0 4px 16px rgba(0,0,0,0.5)",
  },
  mainContent: {
    display: "flex",
    flex: 1,
    minHeight: 0,
    overflow: "hidden",
  },
  // Kanban
  kanbanArea: {
    flex: 7,
    overflow: "auto",
    padding: "16px",
    borderRight: "1px solid #1e1e2e",
  },
  emptyBoard: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    justifyContent: "center",
    height: "100%",
    color: "#8888a0",
    fontSize: "0.95rem",
    textAlign: "center" as const,
  },
  columnsContainer: {
    display: "flex",
    gap: "12px",
    height: "100%",
    minWidth: "min-content",
  },
  // Chat panel
  chatPanel: {
    flex: 3,
    display: "flex",
    flexDirection: "column" as const,
    minWidth: "280px",
    maxWidth: "400px",
  },
  // Lineage
  lineageBar: {
    marginBottom: "12px",
    padding: "8px 12px",
    backgroundColor: "#0a0a14",
    borderRadius: "8px",
    border: "1px solid #1e1e2e",
  },
  lineageToggle: {
    background: "none",
    border: "none",
    color: "#8888a0",
    cursor: "pointer",
    fontSize: "0.78rem",
    padding: 0,
  },
  lineageChain: {
    display: "flex",
    flexWrap: "wrap" as const,
    alignItems: "center",
    gap: "2px",
    marginTop: "8px",
    fontSize: "0.78rem",
  },
  lineageItem: {
    display: "inline-flex",
    alignItems: "center",
  },
  lineageLink: {
    background: "none",
    border: "none",
    color: "#8888a0",
    cursor: "pointer",
    fontSize: "0.78rem",
    padding: "2px 6px",
    borderRadius: "4px",
  },
};
