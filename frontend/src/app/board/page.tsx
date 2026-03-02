"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { GatewayWebSocket, type GatewayMessage } from "@/lib/ws";
import type { WorkItem, WorkPlan, ChatMessage, AgentStatus } from "@/lib/types";
import { STATUS_EMOJI, KANBAN_COLUMNS } from "@/lib/theme";
import ChatPanel from "@/components/shared/ChatPanel";
import PlanSelector from "@/components/shared/PlanSelector";
import KanbanColumn from "@/components/shared/KanbanColumn";

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
  const [plans, setPlans] = useState<WorkPlan[]>([]);
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(
    () => (typeof window !== "undefined" ? searchParams.get("plan") : null)
  );
  const [selectedPlan, setSelectedPlan] = useState<WorkPlan | null>(null);
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
  const [toolActivity, setToolActivity] = useState<{ name: string; args: string; time: number }[]>([]);

  const wsRef = useRef<GatewayWebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
        const def = data.find(a => a.is_default);
        if (def) setSelectedAgentId(def.id);
      })
      .catch(() => {});
  }, []);

  // Fetch plans list (lightweight - no item details)
  const fetchPlans = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/plans?limit=50`);
      const data: WorkPlan[] = await res.json();
      setPlans(data);
      if (!selectedPlanId && data.length > 0) {
        const active = data.find(p => p.status === "active") || data[0];
        setSelectedPlanId(active.id);
      }
    } catch { /* API not available */ }
  }, [selectedPlanId]);

  // Fetch selected plan details
  const fetchPlanDetails = useCallback(async () => {
    if (!selectedPlanId) return;
    try {
      const res = await fetch(`${API_BASE}/plans/${selectedPlanId}`);
      if (res.ok) {
        const data: WorkPlan = await res.json();
        setSelectedPlan(data);
        // Auto-switch chat to plan's conversation (only on initial plan selection, not polls)
        if (data.conversation_id && data.conversation_id !== conversationId && wsRef.current && !selectedPlan) {
          setConversationId(data.conversation_id);
          wsRef.current.switchConversation(data.conversation_id);
        }
      }
    } catch { /* ignore */ }
  }, [selectedPlanId, conversationId]);

  // Fetch plan lineage
  const fetchLineage = useCallback(async () => {
    if (!selectedPlanId) return;
    try {
      const res = await fetch(`${API_BASE}/plans/${selectedPlanId}/lineage`);
      if (res.ok) setLineage(await res.json());
    } catch { /* ignore */ }
  }, [selectedPlanId]);

  useEffect(() => { fetchPlans(); }, [fetchPlans]);
  useEffect(() => { fetchPlanDetails(); }, [fetchPlanDetails]);
  useEffect(() => { if (selectedPlanId) fetchLineage(); }, [selectedPlanId, fetchLineage]);

  // Slow fallback poll every 30 seconds (primary updates via WebSocket events)
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (selectedPlanId) {
      pollRef.current = setInterval(() => {
        fetchPlanDetails();
      }, 30000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [selectedPlanId, fetchPlanDetails]);

  // WebSocket
  useEffect(() => {
    let cancelled = false;
    const ws = new GatewayWebSocket();
    wsRef.current = ws;

    ws.onMessage((msg: GatewayMessage) => {
      if (cancelled) return;
      if (msg.type === "connected") {
        setConnected(true);
        const storedId = localStorage.getItem("bond-conversation-id");
        if (storedId) ws.switchConversation(storedId);
      } else if (msg.type === "status") {
        const status = msg.agentStatus || "idle";
        setAgentStatus(status);
        if (msg.agentName) currentAgentNameRef.current = msg.agentName;
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
        fetchPlans();
        fetchPlanDetails();
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
      // Plan SSE events
      if (msg.type === "plan_created" || msg.type === "plan_updated" || msg.type === "item_updated" || msg.type === "plan_completed") {
        fetchPlans();
        fetchPlanDetails();
        if (msg.type === "plan_created" && msg.planId) {
          setSelectedPlanId(msg.planId);
        }
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
    wsRef.current.send(content, conversationId || undefined, selectedAgentId || undefined);
  }, [input, conversationId, selectedAgentId]);

  const handleStop = useCallback(() => {
    if (!wsRef.current?.connected || !conversationId) return;
    wsRef.current.interrupt(conversationId);
  }, [conversationId]);

  const handlePause = useCallback(async () => {
    if (!conversationId) return;
    wsRef.current?.interrupt(conversationId);
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
      fetchPlans();
      fetchPlanDetails();
    } catch { /* ignore */ }
    if (conversationId) wsRef.current?.interrupt(conversationId);
  }, [selectedPlanId, conversationId, fetchPlans, fetchPlanDetails]);

  // -- Kanban helpers --

  const itemsByColumn = (column: string): WorkItem[] => {
    if (!selectedPlan?.items) return [];
    return selectedPlan.items.filter(item => item.status === column).sort((a, b) => a.ordinal - b.ordinal);
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
      fetchPlanDetails();
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
          plans={plans}
          selectedPlanId={selectedPlanId}
          selectedPlan={selectedPlan}
          onSelect={setSelectedPlanId}
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
                  items={itemsByColumn(col.key)}
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
