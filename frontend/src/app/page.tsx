"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { GatewayWebSocket, type GatewayMessage, type ConversationSummary, type ConnectionState } from "@/lib/ws";
import { GATEWAY_API } from "@/lib/config";
import type { ChatMessage, AgentStatus, PlanCardData } from "@/lib/types";
import ChatPanel from "@/components/shared/ChatPanel";
import PlanCard from "@/components/shared/PlanCard";
import { useSpacetimeConnection, useConversations, useAgents } from "@/hooks/useSpacetimeDB";
import { getAgentName } from "@/lib/spacetimedb-client";
import RestoreDialog from "@/components/RestoreDialog";

function _toolSummary(name: string, data: Record<string, unknown>): string {
  if (name === "file_write" || name === "file_read") {
    const parsed = typeof data.args === "string" ? JSON.parse(data.args as string) : data.args;
    return (parsed as Record<string, string>)?.path || JSON.stringify(data.args).substring(0, 60);
  } else if (name === "code_execute") {
    return "running code...";
  } else if (name === "web_search" || name === "web_read") {
    const parsed = typeof data.args === "string" ? JSON.parse(data.args as string) : data.args;
    return (parsed as Record<string, string>)?.query || (parsed as Record<string, string>)?.url || JSON.stringify(data.args).substring(0, 60);
  } else if (name === "memory_save" || name === "search_memory") {
    return "memory";
  } else if (name === "respond") {
    return "";
  }
  const args = data.args ? (typeof data.args === "string" ? data.args : JSON.stringify(data.args)) : "";
  return (args as string).substring(0, 60);
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [connectionState, setConnectionState] = useState<ConnectionState>("disconnected");
  const [loading, setLoading] = useState(false);
  const [agentStatus, setAgentStatus] = useState<AgentStatus>("idle");
  const [streamingContent, setStreamingContent] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("bond-conversation-id");
    }
    return null;
  });
  // Conversations from SpacetimeDB — auto-updates via subscription
  const spacetimeConversations = useConversations();
  const { connected: stdbConnected } = useSpacetimeConnection();
  const conversations: ConversationSummary[] = spacetimeConversations
    .filter((c) => c.messageCount > 0)
    .map((c) => ({
      id: c.id,
      title: c.title || null,
      message_count: c.messageCount,
      updated_at: new Date(Number(c.updatedAt)).toISOString(),
      agent_id: c.agentId,
      agent_name: getAgentName(c.agentId),
    }));
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const stdbAgents = useAgents();
  const agents = stdbAgents.map(a => ({ id: a.id, display_name: a.displayName, is_default: a.isDefault }));
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const initialAgentSetRef = useRef(false);
  const [agentDropdownOpen, setAgentDropdownOpen] = useState(false);
  const [deleteMode, setDeleteMode] = useState(false);
  const currentAgentNameRef = useRef<string>("Agent");
  const [toolActivity, setToolActivity] = useState<{ name: string; args: string; time: number }[]>([]);
  const [codingAgentActive, setCodingAgentActive] = useState(false);
  const [codingAgentDiffs, setCodingAgentDiffs] = useState<Record<string, { diff: string; count: number }>>({});
  const [codingAgentSummary, setCodingAgentSummary] = useState<string | null>(null);
  const [codingAgentOutput, setCodingAgentOutput] = useState<string[]>([]);
  const [activePlan, setActivePlan] = useState<PlanCardData | null>(null);
  const [showRestoreDialog, setShowRestoreDialog] = useState(false);
  const [toasts, setToasts] = useState<{ id: number; message: string; repo: string; branch: string; actor?: string }[]>([]);
  const toastIdRef = useRef(0);
  const agentDropdownRef = useRef<HTMLDivElement | null>(null);

  const wsRef = useRef<GatewayWebSocket | null>(null);

  // Keep currentAgentNameRef in sync with selected agent
  useEffect(() => {
    const name = agents.find(a => a.id === selectedAgentId)?.display_name;
    if (name) currentAgentNameRef.current = name;
  }, [selectedAgentId, agents]);





  // Show restore dialog when SpacetimeDB is connected but conversations are empty
  useEffect(() => {
    if (stdbConnected && spacetimeConversations.length === 0 && !sessionStorage.getItem("bond-restore-dismissed")) {
      setShowRestoreDialog(true);
    } else if (spacetimeConversations.length > 0) {
      setShowRestoreDialog(false);
    }
  }, [stdbConnected, spacetimeConversations.length]);

  // Persist conversation ID
  useEffect(() => {
    if (conversationId) {
      localStorage.setItem("bond-conversation-id", conversationId);
    } else {
      localStorage.removeItem("bond-conversation-id");
    }
  }, [conversationId]);

  // Ctrl+Shift enables delete mode on messages
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => setDeleteMode(e.ctrlKey && e.shiftKey);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    window.addEventListener("blur", () => setDeleteMode(false));
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      window.removeEventListener("blur", () => setDeleteMode(false));
    };
  }, []);

  const deleteMessage = async (msgId: string, index: number) => {
    if (msgId && conversationId) {
      try {
        await fetch(`${GATEWAY_API}/conversations/${conversationId}/messages/${msgId}`, {
          method: "DELETE",
        });
      } catch { /* best effort */ }
    }
    setMessages((prev) => prev.filter((_, i) => i !== index));
  };

  const resendMessage = (content: string) => {
    if (!wsRef.current?.connected || !content.trim()) return;
    setMessages((prev) => [...prev, { role: "user", content, status: "sending" }]);
    setLoading(true);
    wsRef.current.send(content, conversationId || undefined, selectedAgentId || undefined);
  };

  // Close agent dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (agentDropdownRef.current && !agentDropdownRef.current.contains(e.target as Node)) {
        setAgentDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  // Auto-select default agent when agents load from SpacetimeDB
  useEffect(() => {
    if (agents.length === 0) return;
    const storedConvId = localStorage.getItem("bond-conversation-id");
    if (!storedConvId && !selectedAgentId) {
      const def = agents.find(a => a.is_default);
      if (def) {
        setSelectedAgentId(def.id);
        currentAgentNameRef.current = def.display_name;
      }
    }
  }, [agents.length]);

  useEffect(() => {
    let cancelled = false;
    const ws = new GatewayWebSocket();
    wsRef.current = ws;

    ws.onConnectionChange((state: ConnectionState) => {
      if (cancelled) return;
      setConnectionState(state);
      setConnected(state === "connected");
    });

    ws.onMessage((msg: GatewayMessage) => {
      if (cancelled) return;
      if (msg.type === "connected") {
        setConnected(true);
        ws.listConversations();
        const storedId = localStorage.getItem("bond-conversation-id");
        if (storedId) {
          ws.switchConversation(storedId);
        }
      } else if (msg.type === "status") {
        const status = msg.agentStatus || "idle";
        setAgentStatus(status);
        if (msg.agentName) currentAgentNameRef.current = msg.agentName;
        else {
          const name = agents.find(a => a.id === selectedAgentId)?.display_name;
          if (name) currentAgentNameRef.current = name;
        }
        if (status === "interrupted") {
          // Agent was stopped — keep loading true briefly so progress stays visible
          // until the "done" event arrives to finalize
        } else if (status !== "idle" && status !== "stopping") {
          setLoading(true);
        }
      } else if (msg.type === "tool_call" && msg.content) {
        try {
          const data = JSON.parse(msg.content);
          const name = data.tool_name || data.name || "tool";
          const summary = _toolSummary(name, data);
          setToolActivity((prev) => [...prev, { name, args: summary, time: Date.now() }]);
        } catch { /* ignore parse errors */ }
      } else if (msg.type === "coding_agent_started" && msg.content) {
        setCodingAgentActive(true);
        setCodingAgentDiffs({});
        setCodingAgentOutput([]);
        setCodingAgentSummary(null);
      } else if (msg.type === "coding_agent_diff" && msg.content) {
        try {
          const data = JSON.parse(msg.content);
          const file = data.file as string;
          setCodingAgentDiffs((prev) => ({
            ...prev,
            [file]: {
              diff: data.diff,
              count: (prev[file]?.count || 0) + 1,
            },
          }));
        } catch { /* ignore */ }
      } else if (msg.type === "coding_agent_output" && msg.content) {
        try {
          const data = JSON.parse(msg.content);
          const text = data.text as string;
          if (text) {
            setCodingAgentOutput((prev) => {
              const lines = [...prev, ...text.split("\n")];
              // Keep last 200 lines in state to avoid memory bloat
              return lines.length > 200 ? lines.slice(-200) : lines;
            });
          }
        } catch { /* ignore */ }
      } else if (msg.type === "coding_agent_done" && msg.content) {
        try {
          const data = JSON.parse(msg.content);
          setCodingAgentActive(false);
          setCodingAgentSummary(data.summary || `Coding agent ${data.status} in ${data.elapsed_seconds}s`);
          // Add summary as a chat message
          setMessages((prev) => [...prev, {
            role: "assistant" as const,
            content: data.summary || `Coding agent ${data.status}`,
            agentName: "Coding Agent",
          }]);
        } catch { /* ignore */ }
      } else if (msg.type === "chunk" && msg.content) {
        setStreamingContent((prev) => prev + msg.content!);
        setAgentStatus("responding");
      } else if (msg.type === "done") {
        setStreamingContent((prev) => {
          if (prev) {
            setMessages((msgs) => {
              const last = msgs[msgs.length - 1];
              if (last?.role === "assistant" && last.content === prev) {
                return msgs;
              }
              return [...msgs, { id: msg.messageId, role: "assistant", content: prev, agentName: msg.agentName || currentAgentNameRef.current }];
            });
          }
          return "";
        });
        setLoading(false);
        setAgentStatus("idle");
        setToolActivity([]);
        if (msg.conversationId) {
          setConversationId(msg.conversationId);
        }
        ws.listConversations();
      } else if (msg.type === "queued") {
        setMessages((prev) =>
          prev.map((m, i) =>
            i === prev.length - 1 && m.role === "user" && m.status === "sending"
              ? { ...m, status: "queued" as const }
              : m
          )
        );
      } else if (msg.type === "history" && msg.messages) {
        setMessages(
          msg.messages
            .filter((m) => m.role === "user" || m.role === "assistant" || m.role === "system")
            .map((m) => ({
              id: m.id,
              role: m.role as "user" | "assistant" | "system",
              content: m.content,
            }))
        );
        if (msg.conversationId) {
          setConversationId(msg.conversationId);
        }
        // Switch to the conversation's agent (authoritative — comes from backend)
        if (msg.agentId) {
          setSelectedAgentId(msg.agentId);
        }
      } else if (msg.type === "conversations_list" && msg.conversations) {
        // Legacy: still handle for agent selection init until agents are in SpacetimeDB
        if (!initialAgentSetRef.current) {
          initialAgentSetRef.current = true;
          const storedConvId = localStorage.getItem("bond-conversation-id");
          if (storedConvId) {
            const conv = msg.conversations.find((c: ConversationSummary) => c.id === storedConvId);
            if (conv?.agent_id) setSelectedAgentId(conv.agent_id);
          }
        }
      } else if (msg.type === "error") {
        setMessages((prev) => [
          ...prev,
          { role: "system", content: `Error: ${msg.error || "Unknown error"}` },
        ]);
        setLoading(false);
        setAgentStatus("idle");
      } else if (msg.type === "user_message" && msg.content) {
        // User message sent from another window/tab
        setMessages((prev) => [...prev, { role: "user", content: msg.content! }]);
      }
      // Webhook push toast
      if (msg.type === "webhook_push" && msg.content) {
        try {
          const data = JSON.parse(msg.content);
          const id = ++toastIdRef.current;
          setToasts((prev) => [...prev, {
            id,
            message: `Branch pushed: ${data.branch}`,
            repo: data.repo || "",
            branch: data.branch || "",
            actor: data.actor,
          }]);
          setTimeout(() => {
            setToasts((prev) => prev.filter((t) => t.id !== id));
          }, 6000);
        } catch { /* ignore parse errors */ }
      }

      // Plan events
      if (msg.type === "plan_created" && msg.planId && msg.planTitle) {
        setActivePlan({ id: msg.planId, title: msg.planTitle, status: "active", items: [] });
      } else if (msg.type === "item_updated" && msg.itemId && activePlan) {
        setActivePlan(prev => {
          if (!prev) return prev;
          const items = prev.items.map(it =>
            it.id === msg.itemId ? { ...it, status: msg.itemStatus || it.status } : it
          );
          if (msg.itemId && !items.find(it => it.id === msg.itemId)) {
            items.push({ id: msg.itemId, title: msg.itemTitle || "Item", status: msg.itemStatus || "new" });
          }
          return { ...prev, items };
        });
      } else if (msg.type === "plan_completed" && msg.planId) {
        setActivePlan(prev => prev && prev.id === msg.planId ? { ...prev, status: msg.planStatus || "completed" } : prev);
      }
    });

    ws.connect();

    return () => {
      cancelled = true;
      ws.disconnect();
    };
  }, []);

  const sendMessage = useCallback(() => {
    if (!input.trim() || !wsRef.current?.connected) return;

    const content = input.trim();
    setInput("");

    if (loading && conversationId) {
      // Agent is busy — inject context mid-turn (037 §5.3.3)
      setMessages((prev) => [...prev, { role: "user", content, status: "complete", injected: true }]);
      wsRef.current.inject(conversationId, content);
    } else {
      setMessages((prev) => [...prev, { role: "user", content, status: "sending" }]);
      if (!loading) {
        setLoading(true);
      }
      wsRef.current.send(content, conversationId || undefined, selectedAgentId || undefined);
    }
  }, [input, loading, conversationId, selectedAgentId]);

  const handleStop = useCallback(() => {
    if (!wsRef.current?.connected || !conversationId) return;
    wsRef.current.interrupt(conversationId);
    // Don't setLoading(false) here — wait for the SSE stream to end
    // with a "done" event. The agent status will update via status events.
    setAgentStatus("stopping");
  }, [conversationId]);

  const handleNewConversation = async () => {
    setMessages([]);
    setConversationId(null);
    // Don't pre-create the conversation via REST — let the first message
    // create it through the WebSocket path, which passes the selected
    // agentId correctly. Pre-creating caused a bug where the conversation
    // was created with the default agent before the user picked one.
    wsRef.current?.newConversation();
  };

  const handleSwitchConversation = (id: string) => {
    if (id === conversationId) return;
    setMessages([]);
    setLoading(false);
    const conv = conversations.find(c => c.id === id);
    if (conv?.agent_id) setSelectedAgentId(conv.agent_id);
    wsRef.current?.switchConversation(id);
    // Auto-close sidebar on narrow screens
    if (window.innerWidth < 768) setSidebarOpen(false);
  };

  const handleDeleteConversation = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!(e.ctrlKey && e.shiftKey)) {
      if (!confirm("Delete this conversation?")) return;
    }
    wsRef.current?.deleteConversation(id);
    if (id === conversationId) {
      setMessages([]);
      setConversationId(null);
    }
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    if (diff < 86400000) return "Today";
    if (diff < 172800000) return "Yesterday";
    return d.toLocaleDateString();
  };

  const selectedAgentName = agents.find(a => a.id === selectedAgentId)?.display_name || "Bond";

  return (
    <div style={styles.outerContainer}>
      {/* Mobile responsive overrides */}
      <style>{`
        @media (max-width: 767px) {
          .bond-sidebar {
            position: fixed !important;
            top: 0 !important;
            left: 0 !important;
            bottom: 0 !important;
            z-index: 200 !important;
            width: 280px !important;
          }
          .bond-sidebar.collapsed {
            width: 0px !important;
          }
          .bond-sidebar-overlay {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.5);
            z-index: 199;
          }
          .bond-header-extras {
            display: none !important;
          }
        }
      `}</style>
      {/* Sidebar overlay for mobile */}
      {sidebarOpen && <div className="bond-sidebar-overlay" onClick={() => setSidebarOpen(false)} />}
      {/* Sidebar */}
      <div
        className={`bond-sidebar${sidebarOpen ? "" : " collapsed"}`}
        style={{
          ...styles.sidebar,
          ...(sidebarOpen ? {} : styles.sidebarCollapsed),
        }}
      >
        <div style={styles.sidebarHeader}>
          <button style={styles.newConvButton} onClick={handleNewConversation}>
            + New Conversation
          </button>
        </div>
        <div style={styles.convList}>
          {conversations.map((conv) => (
            <div
              key={conv.id}
              style={{
                ...styles.convItem,
                ...(conv.id === conversationId ? styles.convItemActive : {}),
              }}
              onClick={() => handleSwitchConversation(conv.id)}
              title={conv.title || "New conversation"}
            >
              <div style={styles.convTitle}>
                {conv.title || (conv.agent_name ? `Chat with ${conv.agent_name}` : "New conversation")}
              </div>
              <div style={styles.convMeta}>
                {conv.agent_name && <span style={{ color: "#6c8aff" }}>{conv.agent_name}</span>}
                {conv.agent_name && " · "}
                {formatDate(conv.updated_at)} · {conv.message_count} msgs
              </div>
              <button
                style={deleteMode ? styles.convDeleteBtnDanger : styles.convDeleteBtnCircle}
                onClick={(e) => handleDeleteConversation(conv.id, e)}
                title="Delete conversation"
              >
                X
              </button>
            </div>
          ))}
          {conversations.length === 0 && (
            <div style={styles.convEmpty}>No conversations yet</div>
          )}
        </div>
        {/* Sidebar footer with nav links */}
        <div style={{
          padding: "12px 16px",
          borderTop: "1px solid #1e1e2e",
          display: "flex",
          gap: "12px",
          flexShrink: 0,
        }}>
          <a href="/board" style={{
            flex: 1, textAlign: "center" as const, color: "#8888a0", textDecoration: "none",
            fontSize: "0.8rem", padding: "8px", borderRadius: "8px", border: "1px solid #2a2a3e",
          }}>
            📋 Board
          </a>
          <a href="/settings" style={{
            flex: 1, textAlign: "center" as const, color: "#6c8aff", textDecoration: "none",
            fontSize: "0.8rem", padding: "8px", borderRadius: "8px", border: "1px solid #2a2a3e",
          }}>
            ⚙ Settings
          </a>
        </div>
      </div>

      {/* Main chat area */}
      <div style={styles.container}>
        <header style={styles.header}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <button
              style={styles.sidebarToggle}
              onClick={() => setSidebarOpen(!sidebarOpen)}
              title="Toggle sidebar"
            >
              {sidebarOpen ? "\u2190" : "\u2261"}
            </button>
            <h1 style={styles.title}>{selectedAgentName}</h1>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            {agents.length > 1 && (
              <div ref={agentDropdownRef} style={{ position: "relative" }} className="bond-header-extras">
                <button
                  onClick={() => setAgentDropdownOpen(!agentDropdownOpen)}
                  style={{
                    backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: "8px",
                    padding: "6px 12px", color: "#e0e0e8", fontSize: "0.85rem", cursor: "pointer",
                    display: "flex", alignItems: "center", gap: "6px",
                  }}
                >
                  {agents.find(a => a.id === selectedAgentId)?.display_name || "Select Agent"}
                  <span style={{ fontSize: "0.7rem", color: "#5a5a6e" }}>{agentDropdownOpen ? "\u25B2" : "\u25BC"}</span>
                </button>
                {agentDropdownOpen && (
                  <div style={{
                    position: "absolute", top: "calc(100% + 4px)", right: 0, minWidth: "180px",
                    backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: "10px",
                    overflow: "hidden", zIndex: 100, boxShadow: "0 8px 24px rgba(0,0,0,0.4)",
                  }}>
                    {agents.map((a) => (
                      <div
                        key={a.id}
                        onClick={() => { setSelectedAgentId(a.id); setAgentDropdownOpen(false); }}
                        style={{
                          padding: "10px 14px", cursor: "pointer", fontSize: "0.85rem",
                          color: a.id === selectedAgentId ? "#6c8aff" : "#e0e0e8",
                          backgroundColor: a.id === selectedAgentId ? "#12121a" : "transparent",
                          transition: "background-color 0.15s",
                        }}
                        onMouseEnter={(e) => { if (a.id !== selectedAgentId) (e.target as HTMLElement).style.backgroundColor = "#2a2a3e"; }}
                        onMouseLeave={(e) => { if (a.id !== selectedAgentId) (e.target as HTMLElement).style.backgroundColor = "transparent"; }}
                      >
                        {a.display_name}
                        {a.is_default && <span style={{ color: "#5a5a6e", fontSize: "0.75rem", marginLeft: "6px" }}>default</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            <a href="/board" className="bond-header-extras" style={{
              color: "#8888a0", textDecoration: "none", fontSize: "0.85rem",
              padding: "6px 12px", borderRadius: "8px", border: "1px solid #2a2a3e",
            }}>
              &#x1F4CB; Board
            </a>
            <a href="/settings" className="bond-header-extras" style={{ color: "#6c8aff", textDecoration: "none", fontSize: "0.85rem" }}>
              Settings
            </a>
            <span style={{
              ...styles.status,
              color: connectionState === "connected" ? "#6cffa0"
                : connectionState === "reconnecting" ? "#ffa06c"
                : "#ff6c8a",
            }}>
              {connectionState === "connected" ? "Connected"
                : connectionState === "reconnecting" ? "Reconnecting…"
                : connectionState === "connecting" ? "Connecting…"
                : "Disconnected"}
            </span>
          </div>
        </header>

        {/* Inline Plan Card */}
        {activePlan && (
          <div style={{ padding: "12px 24px 0", display: "flex", justifyContent: "center" }}>
            <PlanCard plan={activePlan} />
          </div>
        )}

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
          codingAgentActive={codingAgentActive}
          codingAgentDiffs={codingAgentDiffs}
          codingAgentOutput={codingAgentOutput}
          compact={false}
          showToolActivityLog={true}
          emptyMessage={`Send a message to start chatting with ${selectedAgentName}.`}
          deleteMode={deleteMode}
          onDeleteMessage={deleteMessage}
          onResendMessage={resendMessage}
          selectedAgentName={selectedAgentName}
        />
      </div>

      {/* Restore dialog */}
      {showRestoreDialog && (
        <RestoreDialog onDismiss={() => setShowRestoreDialog(false)} />
      )}

      {/* Toast notifications */}
      {toasts.length > 0 && (
        <div style={{
          position: "fixed",
          top: "16px",
          right: "16px",
          zIndex: 9999,
          display: "flex",
          flexDirection: "column",
          gap: "8px",
          pointerEvents: "none",
        }}>
          {toasts.map((toast) => (
            <div
              key={toast.id}
              style={{
                background: "linear-gradient(135deg, #1a1a2e 0%, #16213e 100%)",
                border: "1px solid #6c8aff44",
                borderRadius: "12px",
                padding: "12px 16px",
                color: "#e0e0e8",
                fontSize: "0.85rem",
                boxShadow: "0 4px 24px rgba(0,0,0,0.5)",
                pointerEvents: "auto",
                animation: "toastSlideIn 0.3s ease-out",
                maxWidth: "360px",
                display: "flex",
                alignItems: "flex-start",
                gap: "10px",
              }}
            >
              <span style={{ fontSize: "1.1rem", flexShrink: 0 }}>🔀</span>
              <div>
                <div style={{ fontWeight: 600, marginBottom: "2px" }}>
                  {toast.branch}
                </div>
                <div style={{ color: "#8888a0", fontSize: "0.78rem" }}>
                  {toast.actor && <span>{toast.actor} pushed to </span>}
                  {toast.repo}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      <style>{`
        @keyframes toastSlideIn {
          from { opacity: 0; transform: translateX(40px); }
          to { opacity: 1; transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  outerContainer: {
    display: "flex",
    height: "100dvh",
    overflow: "hidden",
  },
  sidebar: {
    width: "280px",
    backgroundColor: "#0a0a0f",
    borderRight: "1px solid #1e1e2e",
    display: "flex",
    flexDirection: "column",
    flexShrink: 0,
    transition: "width 0.2s ease, opacity 0.2s ease",
    overflow: "hidden",
  },
  sidebarCollapsed: {
    width: "0px",
    borderRight: "none",
  },
  /* Applied via className below for mobile overlay behavior */
  sidebarHeader: {
    padding: "16px",
    borderBottom: "1px solid #1e1e2e",
  },
  newConvButton: {
    width: "100%",
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "10px 16px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  convList: {
    flex: 1,
    overflowY: "auto",
    padding: "8px",
  },
  convItem: {
    padding: "12px",
    borderRadius: "8px",
    backgroundColor: "#12121a",
    marginBottom: "4px",
    cursor: "pointer",
    position: "relative" as const,
  },
  convItemActive: {
    borderLeft: "3px solid #6c8aff",
    backgroundColor: "#1a1a2e",
  },
  convTitle: {
    fontSize: "0.85rem",
    color: "#e0e0e8",
    whiteSpace: "nowrap" as const,
    overflow: "hidden",
    textOverflow: "ellipsis",
    paddingRight: "24px",
  },
  convMeta: {
    fontSize: "0.7rem",
    color: "#6668880",
    marginTop: "4px",
  },
  convDeleteBtn: {
    position: "absolute" as const,
    top: "8px",
    right: "8px",
    background: "none",
    border: "none",
    color: "#666",
    cursor: "pointer",
    fontSize: "0.8rem",
    padding: "2px 6px",
    borderRadius: "4px",
  },
  convDeleteBtnCircle: {
    position: "absolute" as const,
    top: "8px",
    right: "8px",
    background: "rgba(100,100,100,0.1)",
    border: "1px solid rgba(100,100,100,0.3)",
    borderRadius: "50%",
    width: "22px",
    height: "22px",
    color: "#666",
    fontSize: "0.75rem",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    lineHeight: 1,
    padding: 0,
  },
  convDeleteBtnDanger: {
    position: "absolute" as const,
    top: "8px",
    right: "8px",
    background: "rgba(255,60,80,0.15)",
    border: "1px solid rgba(255,60,80,0.4)",
    borderRadius: "50%",
    width: "22px",
    height: "22px",
    color: "#ff3c50",
    fontSize: "0.75rem",
    cursor: "pointer",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    lineHeight: 1,
    padding: 0,
  },
  convEmpty: {
    textAlign: "center" as const,
    color: "#555",
    marginTop: "24px",
    fontSize: "0.85rem",
  },
  sidebarToggle: {
    background: "none",
    border: "1px solid #1e1e2e",
    color: "#e0e0e8",
    cursor: "pointer",
    fontSize: "1.2rem",
    padding: "4px 8px",
    borderRadius: "6px",
  },
  container: {
    display: "flex",
    flexDirection: "column",
    flex: 1,
    minWidth: 0,
    overflow: "hidden",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "12px 16px",
    borderBottom: "1px solid #1e1e2e",
    flexShrink: 0,
    gap: "8px",
    zIndex: 50,
    backgroundColor: "#0d0d14",
  },
  title: {
    fontSize: "1.5rem",
    fontWeight: 700,
    margin: 0,
  },
  status: {
    fontSize: "0.85rem",
  },
};
