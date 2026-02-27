"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { GatewayWebSocket, type GatewayMessage, type ConversationSummary } from "@/lib/ws";

// -- Types --

interface WorkItem {
  id: string;
  title: string;
  status: string;
  ordinal: number;
  context_snapshot: Record<string, unknown> | null;
  notes: string[];
  files_changed: string[];
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

interface WorkPlan {
  id: string;
  agent_id: string;
  conversation_id: string | null;
  parent_plan_id: string | null;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
  items?: WorkItem[];
}

type AgentStatus = "idle" | "thinking" | "tool_calling" | "responding";

interface ChatMessage {
  id?: string;
  role: "user" | "assistant" | "system";
  content: string;
  agentName?: string;
}

const API_BASE = "http://localhost:18790/api/v1";

const KANBAN_COLUMNS: { key: string; label: string }[] = [
  { key: "new", label: "New" },
  { key: "in_progress", label: "In Progress" },
  { key: "done", label: "Done" },
  { key: "in_review", label: "In Review" },
  { key: "complete", label: "Complete" },
];

const STATUS_EMOJI: Record<string, string> = {
  active: "\uD83D\uDD04",
  paused: "\u23F8",
  completed: "\u2705",
  failed: "\u274C",
  cancelled: "\uD83D\uDEAB",
};

const ITEM_STATUS_COLORS: Record<string, string> = {
  new: "#8888a0",
  in_progress: "#6c8aff",
  done: "#6cffa0",
  in_review: "#ffcc44",
  approved: "#44ddff",
  in_test: "#ff9944",
  tested: "#44ffbb",
  complete: "#6cffa0",
  blocked: "#ff6c8a",
  failed: "#ff4444",
};

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
  const [planDropdownOpen, setPlanDropdownOpen] = useState(false);
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
  const planDropdownRef = useRef<HTMLDivElement | null>(null);
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

  // Fetch plans list
  const fetchPlans = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/plans?limit=50`);
      const data: WorkPlan[] = await res.json();
      setPlans(data);
      // Auto-select the first active plan if none selected
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
      }
    } catch { /* ignore */ }
  }, [selectedPlanId]);

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

  // Poll plan details every 3 seconds when agent is active
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (selectedPlanId && agentStatus !== "idle") {
      pollRef.current = setInterval(() => {
        fetchPlanDetails();
      }, 3000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [selectedPlanId, agentStatus, fetchPlanDetails]);

  // Close plan dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (planDropdownRef.current && !planDropdownRef.current.contains(e.target as Node)) {
        setPlanDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

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
            setMessages(msgs => [...msgs, { id: msg.messageId, role: "assistant", content: prev, agentName: msg.agentName || currentAgentNameRef.current }]);
          }
          return "";
        });
        setLoading(false);
        setAgentStatus("idle");
        setToolActivity([]);
        if (msg.conversationId) setConversationId(msg.conversationId);
        // Refresh plan data after agent completes
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
      // Plan SSE events — refresh data
      if (msg.type === "plan_created" || msg.type === "plan_updated" || msg.type === "item_updated" || msg.type === "plan_completed") {
        fetchPlans();
        fetchPlanDetails();
        // Auto-select newly created plan
        if (msg.type === "plan_created" && msg.planId) {
          setSelectedPlanId(msg.planId);
        }
      }
    });

    ws.connect();
    return () => { cancelled = true; ws.disconnect(); };
  }, []);

  // Scroll chat to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

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
    // The interrupt will pause the agent; plan status updates via SSE
  }, [conversationId]);

  const handleResume = useCallback(async () => {
    if (!selectedPlanId) return;
    try {
      const res = await fetch(`${API_BASE}/plans/${selectedPlanId}/resume`, { method: "POST" });
      if (res.ok) {
        const data = await res.json();
        // Send recovery context as a message to the agent
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
    // Also stop the agent
    if (conversationId) wsRef.current?.interrupt(conversationId);
  }, [selectedPlanId, conversationId, fetchPlans, fetchPlanDetails]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  // -- Kanban helpers --

  const itemsByColumn = (column: string): WorkItem[] => {
    if (!selectedPlan?.items) return [];
    return selectedPlan.items.filter(item => item.status === column).sort((a, b) => a.ordinal - b.ordinal);
  };

  const timeInStatus = (item: WorkItem): string => {
    const start = item.started_at || item.created_at;
    if (!start) return "";
    const ms = Date.now() - new Date(start).getTime();
    if (ms < 60000) return "<1m";
    if (ms < 3600000) return `${Math.floor(ms / 60000)}m`;
    if (ms < 86400000) return `${Math.floor(ms / 3600000)}h`;
    return `${Math.floor(ms / 86400000)}d`;
  };

  const planIsActive = selectedPlan?.status === "active" || selectedPlan?.status === "paused";

  // Drag and drop handlers
  const handleDragStart = (itemId: string) => {
    setDragItemId(itemId);
  };

  const handleDragOver = (e: React.DragEvent, columnKey: string) => {
    e.preventDefault();
    setDragOverColumn(columnKey);
  };

  const handleDragLeave = () => {
    setDragOverColumn(null);
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

  // Determine if FAB should show (pause when active, resume when paused)
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
        <div className="board-plan-selector" ref={planDropdownRef} style={{ position: "relative", flex: 1, maxWidth: "500px", margin: "0 16px" }}>
          <button
            onClick={() => setPlanDropdownOpen(!planDropdownOpen)}
            style={s.planSelectorBtn}
          >
            <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              {selectedPlan ? `${STATUS_EMOJI[selectedPlan.status] || ""} ${selectedPlan.title}` : "Select a plan..."}
            </span>
            <span style={{ fontSize: "0.7rem", color: "#5a5a6e", flexShrink: 0 }}>{planDropdownOpen ? "\u25B2" : "\u25BC"}</span>
          </button>
          {planDropdownOpen && (
            <div style={s.planDropdown}>
              {plans.length === 0 && (
                <div style={{ padding: "12px 14px", color: "#5a5a6e", fontSize: "0.85rem" }}>
                  No plans yet
                </div>
              )}
              {plans.map(p => (
                <div
                  key={p.id}
                  onClick={() => { setSelectedPlanId(p.id); setPlanDropdownOpen(false); }}
                  style={{
                    padding: "10px 14px",
                    cursor: "pointer",
                    fontSize: "0.85rem",
                    color: p.id === selectedPlanId ? "#6c8aff" : "#e0e0e8",
                    backgroundColor: p.id === selectedPlanId ? "#12121a" : "transparent",
                    display: "flex",
                    gap: "8px",
                    alignItems: "center",
                  }}
                  onMouseEnter={e => { if (p.id !== selectedPlanId) (e.currentTarget as HTMLElement).style.backgroundColor = "#2a2a3e"; }}
                  onMouseLeave={e => { if (p.id !== selectedPlanId) (e.currentTarget as HTMLElement).style.backgroundColor = "transparent"; }}
                >
                  <span>{STATUS_EMOJI[p.status] || "\uD83D\uDCCB"}</span>
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.title}</span>
                </div>
              ))}
            </div>
          )}
        </div>

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
              {KANBAN_COLUMNS.map(col => {
                const items = itemsByColumn(col.key);
                const isDropTarget = dragOverColumn === col.key;
                return (
                  <div
                    key={col.key}
                    className="board-column"
                    style={{
                      ...s.column,
                      ...(isDropTarget ? { backgroundColor: "#12121f", outline: "2px dashed #6c8aff" } : {}),
                    }}
                    onDragOver={e => handleDragOver(e, col.key)}
                    onDragLeave={handleDragLeave}
                    onDrop={() => handleDrop(col.key)}
                  >
                    <div style={s.columnHeader}>
                      <span>{col.label}</span>
                      <span style={s.columnCount}>{items.length}</span>
                    </div>
                    <div style={s.columnBody}>
                      {items.map(item => (
                        <div
                          key={item.id}
                          draggable
                          onDragStart={() => handleDragStart(item.id)}
                          onDragEnd={() => { setDragItemId(null); setDragOverColumn(null); }}
                          style={{
                            ...s.card,
                            borderLeftColor: ITEM_STATUS_COLORS[item.status] || "#5a5a6e",
                            opacity: dragItemId === item.id ? 0.5 : 1,
                            cursor: "grab",
                          }}
                          onClick={() => setExpandedItemId(expandedItemId === item.id ? null : item.id)}
                        >
                          <div style={s.cardTitle}>{item.title}</div>
                          <div style={s.cardMeta}>
                            {timeInStatus(item) && <span>{timeInStatus(item)}</span>}
                            {item.notes.length > 0 && (
                              <span>{item.notes.length} note{item.notes.length !== 1 ? "s" : ""}</span>
                            )}
                            {item.files_changed.length > 0 && (
                              <span>{item.files_changed.length} file{item.files_changed.length !== 1 ? "s" : ""}</span>
                            )}
                          </div>
                          {/* Expanded detail */}
                          {expandedItemId === item.id && (
                            <div style={s.cardDetail}>
                              {item.notes.length > 0 && (
                                <div style={s.detailSection}>
                                  <div style={s.detailLabel}>Notes</div>
                                  {item.notes.map((note, i) => (
                                    <div key={i} style={s.noteItem}>{note}</div>
                                  ))}
                                </div>
                              )}
                              {item.files_changed.length > 0 && (
                                <div style={s.detailSection}>
                                  <div style={s.detailLabel}>Files Changed</div>
                                  {item.files_changed.map((f, i) => (
                                    <div key={i} style={s.fileItem}>{f}</div>
                                  ))}
                                </div>
                              )}
                              {item.context_snapshot && (
                                <div style={s.detailSection}>
                                  <div style={s.detailLabel}>Context Snapshot</div>
                                  <pre style={s.snapshotPre}>
                                    {JSON.stringify(item.context_snapshot, null, 2)}
                                  </pre>
                                </div>
                              )}
                            </div>
                          )}
                        </div>
                      ))}
                      {items.length === 0 && (
                        <div style={s.columnEmpty}>No items</div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Chat Panel */}
        <div className="board-chat-panel" style={s.chatPanel}>
          <div style={s.chatHeader}>
            Agent Chat
            {agentStatus !== "idle" && (
              <span style={s.agentStatusBadge}>
                {agentStatus === "thinking" ? "Thinking..." : agentStatus === "tool_calling" ? "Using tools..." : "Responding..."}
              </span>
            )}
          </div>
          <div style={s.chatMessages}>
            {messages.length === 0 && !streamingContent && (
              <div style={s.chatEmpty}>
                Send a message to start a task.
              </div>
            )}
            {messages.map((msg, i) => (
              <div key={msg.id || i} style={{ ...s.chatMsg, ...(msg.role === "user" ? s.chatMsgUser : {}) }}>
                <div style={s.chatMsgRole}>
                  {msg.role === "user" ? "You" : msg.agentName || "Agent"}
                </div>
                <div style={s.chatMsgContent}>{msg.content}</div>
              </div>
            ))}
            {(loading || streamingContent) && (
              <div style={s.chatMsg}>
                <div style={s.chatMsgRole}>{currentAgentNameRef.current}</div>
                <div style={{ ...s.chatMsgContent, color: streamingContent ? "#e0e0e8" : "#8888a0" }}>
                  {streamingContent || (
                    agentStatus === "tool_calling" ? "Using tools..." :
                    agentStatus === "responding" ? "Responding..." :
                    "Thinking..."
                  )}
                </div>
                {toolActivity.length > 0 && !streamingContent && (
                  <div style={{ marginTop: "6px", fontSize: "0.75rem", color: "#5a5a6e" }}>
                    {toolActivity.length} tool call{toolActivity.length !== 1 ? "s" : ""} &middot; {toolActivity[toolActivity.length - 1].name}
                  </div>
                )}
              </div>
            )}
            <div ref={messagesEndRef} />
          </div>
          <div style={s.chatInputArea}>
            <textarea
              style={s.chatTextarea}
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={loading ? "Type to queue..." : "Type a message..."}
              rows={1}
              disabled={!connected}
            />
            {loading ? (
              <button style={s.chatStopBtn} onClick={handleStop} title="Stop">
                Stop
              </button>
            ) : (
              <button
                style={{ ...s.chatSendBtn, opacity: !input.trim() || !connected ? 0.5 : 1 }}
                onClick={sendMessage}
                disabled={!input.trim() || !connected}
              >
                Send
              </button>
            )}
          </div>
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
  planSelectorBtn: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "8px 14px",
    color: "#e0e0e8",
    fontSize: "0.9rem",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    gap: "8px",
    textAlign: "left" as const,
  },
  planDropdown: {
    position: "absolute" as const,
    top: "calc(100% + 4px)",
    left: 0,
    right: 0,
    maxHeight: "300px",
    overflowY: "auto" as const,
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "10px",
    zIndex: 100,
    boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
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
  column: {
    flex: 1,
    minWidth: "180px",
    maxWidth: "280px",
    display: "flex",
    flexDirection: "column" as const,
    backgroundColor: "#0a0a14",
    borderRadius: "10px",
    overflow: "hidden",
  },
  columnHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
    padding: "10px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    color: "#8888a0",
    textTransform: "uppercase" as const,
    letterSpacing: "0.05em",
    borderBottom: "1px solid #1e1e2e",
  },
  columnCount: {
    backgroundColor: "#1e1e2e",
    borderRadius: "10px",
    padding: "2px 8px",
    fontSize: "0.75rem",
    color: "#5a5a6e",
  },
  columnBody: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "8px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "8px",
  },
  columnEmpty: {
    textAlign: "center" as const,
    color: "#3a3a4e",
    fontSize: "0.8rem",
    padding: "16px 8px",
  },
  card: {
    backgroundColor: "#12121a",
    borderRadius: "8px",
    padding: "10px 12px",
    borderLeft: "3px solid #5a5a6e",
    cursor: "pointer",
    transition: "background-color 0.15s",
  },
  cardTitle: {
    fontSize: "0.85rem",
    color: "#e0e0e8",
    lineHeight: 1.3,
  },
  cardMeta: {
    display: "flex",
    gap: "8px",
    fontSize: "0.7rem",
    color: "#5a5a6e",
    marginTop: "6px",
  },
  cardDetail: {
    marginTop: "10px",
    paddingTop: "10px",
    borderTop: "1px solid #1e1e2e",
  },
  detailSection: {
    marginBottom: "8px",
  },
  detailLabel: {
    fontSize: "0.7rem",
    fontWeight: 600,
    color: "#6c8aff",
    marginBottom: "4px",
    textTransform: "uppercase" as const,
  },
  noteItem: {
    fontSize: "0.78rem",
    color: "#aaa",
    padding: "4px 0",
    borderBottom: "1px solid #1a1a2a",
    whiteSpace: "pre-wrap" as const,
  },
  fileItem: {
    fontSize: "0.78rem",
    color: "#6cffa0",
    fontFamily: "monospace",
    padding: "2px 0",
  },
  snapshotPre: {
    fontSize: "0.72rem",
    color: "#8888a0",
    backgroundColor: "#0a0a14",
    borderRadius: "6px",
    padding: "8px",
    overflow: "auto" as const,
    maxHeight: "200px",
    margin: 0,
  },
  // Chat panel
  chatPanel: {
    flex: 3,
    display: "flex",
    flexDirection: "column" as const,
    minWidth: "280px",
    maxWidth: "400px",
  },
  chatHeader: {
    padding: "10px 16px",
    borderBottom: "1px solid #1e1e2e",
    fontSize: "0.85rem",
    fontWeight: 600,
    color: "#8888a0",
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  agentStatusBadge: {
    fontSize: "0.72rem",
    color: "#6c8aff",
    animation: "pulse 1.5s ease-in-out infinite",
  },
  chatMessages: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "12px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "10px",
  },
  chatEmpty: {
    textAlign: "center" as const,
    color: "#5a5a6e",
    marginTop: "40%",
    fontSize: "0.85rem",
  },
  chatMsg: {
    padding: "8px 12px",
    borderRadius: "8px",
    backgroundColor: "#12121a",
    maxWidth: "95%",
  },
  chatMsgUser: {
    alignSelf: "flex-end",
    backgroundColor: "#1a2a3a",
  },
  chatMsgRole: {
    fontSize: "0.68rem",
    color: "#6c8aff",
    marginBottom: "3px",
    fontWeight: 600,
  },
  chatMsgContent: {
    whiteSpace: "pre-wrap" as const,
    lineHeight: 1.5,
    fontSize: "0.85rem",
  },
  chatInputArea: {
    display: "flex",
    gap: "8px",
    padding: "10px 12px",
    borderTop: "1px solid #1e1e2e",
  },
  chatTextarea: {
    flex: 1,
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "8px",
    padding: "8px 12px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    fontFamily: "inherit",
    resize: "none" as const,
    outline: "none",
  },
  chatSendBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  chatStopBtn: {
    backgroundColor: "#ff4444",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
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
