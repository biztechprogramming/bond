"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { GatewayWebSocket, type GatewayMessage } from "@/lib/ws";

interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export default function Home() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(false);
  const wsRef = useRef<GatewayWebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const ws = new GatewayWebSocket();
    wsRef.current = ws;

    ws.onMessage((msg: GatewayMessage) => {
      if (msg.type === "connected") {
        setConnected(true);
      } else if (msg.type === "response" && msg.content) {
        setMessages((prev) => [...prev, { role: "assistant", content: msg.content! }]);
        setLoading(false);
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
    wsRef.current.send(content);
  }, [input, loading]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <h1 style={styles.title}>Bond</h1>
        <span style={{
          ...styles.status,
          color: connected ? "#6cffa0" : "#ff6c8a",
        }}>
          {connected ? "Connected" : "Connecting..."}
        </span>
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
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    maxWidth: "800px",
    margin: "0 auto",
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
