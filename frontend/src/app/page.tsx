"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { GatewayWebSocket, type GatewayMessage, type ConversationSummary } from "@/lib/ws";

interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(() => {
    if (typeof window !== "undefined") {
      return localStorage.getItem("bond-conversation-id");
    }
    return null;
  });
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const wsRef = useRef<GatewayWebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  // Persist conversation ID
  useEffect(() => {
    if (conversationId) {
      localStorage.setItem("bond-conversation-id", conversationId);
    } else {
      localStorage.removeItem("bond-conversation-id");
    }
  }, [conversationId]);

  useEffect(() => {
    const ws = new GatewayWebSocket();
    wsRef.current = ws;

    ws.onMessage((msg: GatewayMessage) => {
      if (msg.type === "connected") {
        setConnected(true);
        // Request conversation list
        ws.listConversations();
        // If we have a stored conversation, load it
        const storedId = localStorage.getItem("bond-conversation-id");
        if (storedId) {
          ws.switchConversation(storedId);
        }
      } else if (msg.type === "response" && msg.content) {
        setMessages((prev) => [...prev, { role: "assistant", content: msg.content! }]);
        setLoading(false);
        if (msg.conversationId) {
          setConversationId(msg.conversationId);
        }
        // Refresh conversation list
        ws.listConversations();
      } else if (msg.type === "history" && msg.messages) {
        setMessages(
          msg.messages
            .filter((m) => m.role === "user" || m.role === "assistant")
            .map((m) => ({
              role: m.role as "user" | "assistant",
              content: m.content,
            }))
        );
        if (msg.conversationId) {
          setConversationId(msg.conversationId);
        }
      } else if (msg.type === "conversations_list" && msg.conversations) {
        setConversations(msg.conversations);
      } else if (msg.type === "error") {
        setMessages((prev) => [
          ...prev,
          { role: "system", content: `Error: ${msg.error || "Unknown error"}` },
        ]);
        setLoading(false);
      }
    });

    ws.connect();

    return () => {
      ws.disconnect();
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const sendMessage = useCallback(() => {
    if (!input.trim() || !wsRef.current?.connected || loading) return;

    const content = input.trim();
    setInput("");
    setMessages((prev) => [...prev, { role: "user", content }]);
    setLoading(true);
    wsRef.current.send(content, conversationId || undefined);
  }, [input, loading, conversationId]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const handleNewConversation = () => {
    setMessages([]);
    setConversationId(null);
    wsRef.current?.newConversation();
  };

  const handleSwitchConversation = (id: string) => {
    if (id === conversationId) return;
    setMessages([]);
    setLoading(false);
    wsRef.current?.switchConversation(id);
  };

  const handleDeleteConversation = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("Delete this conversation?")) return;
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

  return (
    <div style={styles.outerContainer}>
      {/* Sidebar */}
      <div style={{
        ...styles.sidebar,
        ...(sidebarOpen ? {} : styles.sidebarCollapsed),
      }}>
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
            >
              <div style={styles.convTitle}>
                {conv.title || "New conversation"}
              </div>
              <div style={styles.convMeta}>
                {formatDate(conv.updated_at)} &middot; {conv.message_count} msgs
              </div>
              <button
                style={styles.convDeleteBtn}
                onClick={(e) => handleDeleteConversation(conv.id, e)}
                title="Delete conversation"
              >
                x
              </button>
            </div>
          ))}
          {conversations.length === 0 && (
            <div style={styles.convEmpty}>No conversations yet</div>
          )}
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
            <h1 style={styles.title}>Bond</h1>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            <a href="/settings" style={{ color: "#6c8aff", textDecoration: "none", fontSize: "0.85rem" }}>
              Settings
            </a>
            <span style={{
              ...styles.status,
              color: connected ? "#6cffa0" : "#ff6c8a",
            }}>
              {connected ? "Connected" : "Connecting..."}
            </span>
          </div>
        </header>

        <div style={styles.messages}>
          {messages.length === 0 && (
            <div style={styles.empty}>
              Send a message to start chatting with Bond.
            </div>
          )}
          {messages.map((msg, i) => (
            <div
              key={i}
              style={{
                ...styles.message,
                ...(msg.role === "user" ? styles.userMessage : {}),
                ...(msg.role === "system" ? styles.systemMessage : {}),
              }}
            >
              <div style={styles.messageRole}>
                {msg.role === "user" ? "You" : msg.role === "assistant" ? "Bond" : "System"}
              </div>
              <div style={styles.messageContent}>{msg.content}</div>
            </div>
          ))}
          {loading && (
            <div style={styles.message}>
              <div style={styles.messageRole}>Bond</div>
              <div style={{ ...styles.messageContent, color: "#8888a0" }}>Thinking...</div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        <div style={styles.inputArea}>
          <textarea
            style={styles.textarea}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type a message..."
            rows={1}
            disabled={!connected}
          />
          <button
            style={{
              ...styles.sendButton,
              opacity: !input.trim() || !connected || loading ? 0.5 : 1,
            }}
            onClick={sendMessage}
            disabled={!input.trim() || !connected || loading}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  outerContainer: {
    display: "flex",
    height: "100vh",
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
    height: "100vh",
    flex: 1,
    minWidth: 0,
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 24px",
    borderBottom: "1px solid #1e1e2e",
  },
  title: {
    fontSize: "1.5rem",
    fontWeight: 700,
    margin: 0,
  },
  status: {
    fontSize: "0.85rem",
  },
  messages: {
    flex: 1,
    overflowY: "auto",
    padding: "24px",
    display: "flex",
    flexDirection: "column",
    gap: "16px",
  },
  empty: {
    textAlign: "center",
    color: "#8888a0",
    marginTop: "40vh",
  },
  message: {
    padding: "12px 16px",
    borderRadius: "12px",
    backgroundColor: "#12121a",
    maxWidth: "85%",
  },
  userMessage: {
    alignSelf: "flex-end",
    backgroundColor: "#1a2a3a",
  },
  systemMessage: {
    alignSelf: "center",
    backgroundColor: "#2a1a1a",
    fontSize: "0.85rem",
  },
  messageRole: {
    fontSize: "0.75rem",
    color: "#6c8aff",
    marginBottom: "4px",
    fontWeight: 600,
  },
  messageContent: {
    whiteSpace: "pre-wrap",
    lineHeight: 1.6,
  },
  inputArea: {
    display: "flex",
    gap: "12px",
    padding: "16px 24px",
    borderTop: "1px solid #1e1e2e",
  },
  textarea: {
    flex: 1,
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "12px",
    padding: "12px 16px",
    color: "#e0e0e8",
    fontSize: "1rem",
    fontFamily: "inherit",
    resize: "none",
    outline: "none",
  },
  sendButton: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "12px",
    padding: "12px 24px",
    fontSize: "1rem",
    fontWeight: 600,
    cursor: "pointer",
  },
};
