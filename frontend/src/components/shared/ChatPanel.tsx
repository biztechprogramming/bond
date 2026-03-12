import React, { useRef, useEffect } from "react";
import type { ChatMessage, AgentStatus } from "@/lib/types";
import { toolIcon } from "@/lib/theme";

interface ChatPanelProps {
  messages: ChatMessage[];
  input: string;
  onInputChange: (value: string) => void;
  onSend: () => void;
  onStop?: () => void;
  onPause?: () => void;
  onResume?: () => void;
  onCancel?: () => void;
  connected: boolean;
  loading: boolean;
  agentStatus: AgentStatus;
  streamingContent: string;
  currentAgentName: string;
  toolActivity: { name: string; args: string; time: number }[];
  /** Whether a coding agent is currently running */
  codingAgentActive?: boolean;
  /** Per-file diffs from the coding agent: { filepath: { diff, count } } */
  codingAgentDiffs?: Record<string, { diff: string; count: number }>;
  /** Compact mode for board sidebar */
  compact?: boolean;
  /** Show pause/resume/cancel controls */
  showControls?: boolean;
  /** Show tool activity log */
  showToolActivityLog?: boolean;
  placeholder?: string;
  emptyMessage?: string;
  /** For delete mode in main chat */
  deleteMode?: boolean;
  onDeleteMessage?: (msgId: string, index: number) => void;
  onResendMessage?: (content: string) => void;
  /** Agent name for display */
  selectedAgentName?: string;
}

export default function ChatPanel({
  messages,
  input,
  onInputChange,
  onSend,
  onStop,
  connected,
  loading,
  agentStatus,
  streamingContent,
  currentAgentName,
  toolActivity,
  compact = false,
  showToolActivityLog = false,
  placeholder,
  emptyMessage,
  codingAgentActive = false,
  codingAgentDiffs = {},
  deleteMode = false,
  onDeleteMessage,
  onResendMessage,
  selectedAgentName,
}: ChatPanelProps) {
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const [showToolLog, setShowToolLog] = React.useState(false);
  const [expandedFiles, setExpandedFiles] = React.useState<Set<string>>(new Set());
  const [copiedIdx, setCopiedIdx] = React.useState<number | null>(null);
  const [hoveredIdx, setHoveredIdx] = React.useState<number | null>(null);

  const copyMessage = (content: string, idx: number) => {
    navigator.clipboard.writeText(content).then(() => {
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx(null), 1500);
    });
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  const toggleFile = (filepath: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(filepath)) next.delete(filepath);
      else next.add(filepath);
      return next;
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      onSend();
    }
  };

  const s = compact ? compactStyles : fullStyles;

  return (
    <>
      {compact && (
        <div style={s.chatHeader}>
          Agent Chat
          {agentStatus !== "idle" && (
            <span style={s.agentStatusBadge}>
              {agentStatus === "thinking" ? "Thinking..." : agentStatus === "tool_calling" ? "Using tools..." : agentStatus === "stopping" ? "Stopping..." : agentStatus === "interrupted" ? "Stopped" : "Responding..."}
            </span>
          )}
        </div>
      )}
      <div style={s.chatMessages}>
        {messages.length === 0 && !streamingContent && (
          <div style={s.chatEmpty}>
            {emptyMessage || "Send a message to start chatting."}
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={msg.id ? `${msg.id}-${i}` : i}
            style={{
              ...s.chatMsg,
              ...(msg.role === "user" ? s.chatMsgUser : {}),
              ...(msg.role === "system" ? s.chatMsgSystem : {}),
              position: "relative" as const,
            }}
            onMouseEnter={() => setHoveredIdx(i)}
            onMouseLeave={() => setHoveredIdx(null)}
          >
            {!deleteMode && (
              <button
                onClick={() => copyMessage(msg.content, i)}
                title="Copy message"
                style={{
                  position: "absolute",
                  top: "6px",
                  right: "6px",
                  background: "none",
                  border: "none",
                  color: copiedIdx === i ? "#4ec994" : "#5a5a6e",
                  fontSize: "0.8rem",
                  cursor: "pointer",
                  padding: "2px 4px",
                  borderRadius: "4px",
                  opacity: hoveredIdx === i ? 1 : 0.4,
                  transition: "opacity 0.15s, color 0.15s",
                  lineHeight: 1,
                }}
              >
                {copiedIdx === i ? "✓" : "⧉"}
              </button>
            )}
            {deleteMode && (
              <div style={{ position: "absolute", top: "6px", right: "6px", display: "flex", gap: "4px" }}>
                {msg.role === "user" && onResendMessage && (
                  <button
                    onClick={() => onResendMessage(msg.content)}
                    style={s.deleteActionBtn}
                    title="Resend message"
                  >
                    ↻
                  </button>
                )}
                {onDeleteMessage && (
                  <button
                    onClick={() => onDeleteMessage(msg.id || "", i)}
                    style={s.deleteBtn}
                    title="Delete message"
                  >
                    ✕
                  </button>
                )}
              </div>
            )}
            <div style={s.chatMsgRole}>
              {msg.role === "user" ? "You" : msg.role === "assistant" ? (msg.agentName || selectedAgentName || "Agent") : "System"}
            </div>
            <div style={s.chatMsgContent}>{msg.content}</div>
          </div>
        ))}
        {(loading || streamingContent) && (
          <div style={s.chatMsg}>
            <div style={s.chatMsgRole}>{currentAgentName}</div>
            <div style={{ ...s.chatMsgContent, color: streamingContent ? "#e0e0e8" : "#8888a0" }}>
              {streamingContent || (
                agentStatus === "tool_calling" ? "Using tools..." :
                agentStatus === "responding" ? "Responding..." :
                agentStatus === "stopping" ? "Stopping..." :
                agentStatus === "interrupted" ? "Stopped" :
                "Thinking..."
              )}
            </div>
            {toolActivity.length > 0 && !streamingContent && (
              <div style={{ marginTop: compact ? "6px" : "8px" }}>
                {compact ? (
                  <div style={{ fontSize: "0.75rem", color: "#5a5a6e" }}>
                    {toolActivity.length} tool call{toolActivity.length !== 1 ? "s" : ""} &middot; {toolActivity[toolActivity.length - 1].name}
                  </div>
                ) : (
                  <>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "0.8rem", color: "#6c8aff" }}>
                      <span style={{ animation: "pulse 1.5s ease-in-out infinite", display: "inline-block" }}>●</span>
                      <span>{toolIcon(toolActivity[toolActivity.length - 1].name)} {toolActivity[toolActivity.length - 1].name}</span>
                      {toolActivity[toolActivity.length - 1].args && (
                        <span style={{ color: "#5a5a6e", fontFamily: "monospace", fontSize: "0.75rem" }}>
                          {toolActivity[toolActivity.length - 1].args}
                        </span>
                      )}
                      <span style={{ color: "#5a5a6e", marginLeft: "auto" }}>
                        {toolActivity.length} tool call{toolActivity.length !== 1 ? "s" : ""}
                      </span>
                    </div>
                    {showToolActivityLog && (
                      <>
                        <button
                          onClick={() => setShowToolLog(!showToolLog)}
                          style={{ background: "none", border: "none", color: "#5a5a6e", fontSize: "0.75rem", cursor: "pointer", padding: "4px 0", marginTop: "4px" }}
                        >
                          {showToolLog ? "\u25BC Hide activity" : "\u25B6 Show activity log"}
                        </button>
                        {showToolLog && (
                          <div style={{
                            maxHeight: "200px", overflowY: "auto", fontSize: "0.75rem", fontFamily: "monospace",
                            backgroundColor: "#0a0a14", borderRadius: "6px", padding: "8px", marginTop: "4px",
                          }}>
                            {toolActivity.map((t, idx) => (
                              <div key={idx} style={{ padding: "2px 0", color: idx === toolActivity.length - 1 ? "#6c8aff" : "#5a5a6e" }}>
                                <span style={{ color: "#3a3a4e", marginRight: "6px" }}>{idx + 1}.</span>
                                {toolIcon(t.name)} {t.name}
                                {t.args && <span style={{ color: "#3a3a4e", marginLeft: "6px" }}>{t.args}</span>}
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    )}
                  </>
                )}
              </div>
            )}
          </div>
        )}
        {(codingAgentActive || Object.keys(codingAgentDiffs).length > 0) && (
          <div style={{ ...s.chatMsg, maxWidth: "100%", padding: 0, overflow: "hidden" }}>
            <div style={{
              display: "flex", alignItems: "center", gap: "8px",
              padding: "8px 12px", borderBottom: "1px solid #1e1e2e",
              fontSize: "0.8rem", color: "#6c8aff",
            }}>
              {codingAgentActive && (
                <span style={{ animation: "pulse 1.5s ease-in-out infinite", display: "inline-block" }}>●</span>
              )}
              <span>{codingAgentActive ? "Coding Agent Working" : "Coding Agent — Changes"}</span>
              <span style={{ color: "#5a5a6e", marginLeft: "auto", fontSize: "0.72rem" }}>
                {Object.keys(codingAgentDiffs).length} file{Object.keys(codingAgentDiffs).length !== 1 ? "s" : ""}
              </span>
            </div>
            {Object.keys(codingAgentDiffs).length > 0 && (
              <div style={{ padding: "4px 0" }}>
                {Object.entries(codingAgentDiffs).map(([filepath, { diff, count }]) => (
                  <div key={filepath}>
                    <button
                      onClick={() => toggleFile(filepath)}
                      style={{
                        display: "flex", alignItems: "center", gap: "6px",
                        width: "100%", padding: "6px 12px",
                        background: "none", border: "none", borderBottom: "1px solid #0a0a14",
                        color: "#c0c0d0", fontSize: "0.78rem", cursor: "pointer",
                        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                        textAlign: "left",
                      }}
                    >
                      <span style={{ color: "#5a5a6e", fontSize: "0.7rem" }}>
                        {expandedFiles.has(filepath) ? "▼" : "▶"}
                      </span>
                      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {filepath}
                      </span>
                      {count > 1 && (
                        <span style={{
                          backgroundColor: "#6c8aff",
                          color: "#fff",
                          borderRadius: "8px",
                          padding: "1px 6px",
                          fontSize: "0.65rem",
                          fontWeight: 700,
                          fontFamily: "inherit",
                          minWidth: "18px",
                          textAlign: "center",
                        }}>
                          {count}
                        </span>
                      )}
                    </button>
                    {expandedFiles.has(filepath) && (
                      <div style={{
                        maxHeight: "300px", overflowY: "auto", overflowX: "auto",
                        padding: "8px 12px",
                        backgroundColor: "#0a0a0f",
                        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                        fontSize: "0.72rem", lineHeight: 1.5,
                        whiteSpace: "pre",
                      }}>
                        {diff.split("\n").map((line, i) => (
                          <div key={i} style={{
                            color: line.startsWith("+") && !line.startsWith("+++") ? "#4ec994"
                              : line.startsWith("-") && !line.startsWith("---") ? "#ff6b6b"
                              : line.startsWith("@@") ? "#6c8aff"
                              : "#8888a0",
                          }}>
                            {line}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                ))}
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
          onChange={e => onInputChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || (loading ? "Add context... (agent is working)" : "Type a message...")}
          rows={1}
          disabled={!connected}
        />
        {loading && onStop ? (
          <>
            {input.trim() && (
              <button
                style={{ ...s.chatSendBtn, marginRight: 4 }}
                onClick={onSend}
                disabled={!input.trim() || !connected}
              >
                Send
              </button>
            )}
            <button style={s.chatStopBtn} onClick={onStop} title="Stop">
              ⏹
            </button>
          </>
        ) : (
          <button
            style={{ ...s.chatSendBtn, opacity: !input.trim() || !connected ? 0.5 : 1 }}
            onClick={onSend}
            disabled={!input.trim() || !connected}
          >
            Send
          </button>
        )}
      </div>
    </>
  );
}

const compactStyles: Record<string, React.CSSProperties> = {
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
  chatMsgSystem: {
    alignSelf: "center",
    backgroundColor: "#2a1a1a",
    fontSize: "0.85rem",
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
  deleteActionBtn: {
    background: "rgba(108,138,255,0.15)", border: "1px solid rgba(108,138,255,0.4)",
    borderRadius: "50%", width: "22px", height: "22px",
    color: "#6c8aff", fontSize: "0.75rem", cursor: "pointer",
    display: "flex", alignItems: "center", justifyContent: "center",
    lineHeight: 1, padding: 0,
  },
  deleteBtn: {
    background: "rgba(255,60,80,0.15)", border: "1px solid rgba(255,60,80,0.4)",
    borderRadius: "50%", width: "22px", height: "22px",
    color: "#ff3c50", fontSize: "0.75rem", cursor: "pointer",
    display: "flex", alignItems: "center", justifyContent: "center",
    lineHeight: 1, padding: 0,
  },
};

const fullStyles: Record<string, React.CSSProperties> = {
  ...compactStyles,
  chatMessages: {
    flex: 1,
    overflowY: "auto" as const,
    padding: "24px",
    display: "flex",
    flexDirection: "column" as const,
    gap: "16px",
  },
  chatEmpty: {
    textAlign: "center" as const,
    color: "#8888a0",
    marginTop: "40vh",
  },
  chatMsg: {
    padding: "12px 16px",
    borderRadius: "12px",
    backgroundColor: "#12121a",
    maxWidth: "85%",
  },
  chatMsgRole: {
    fontSize: "0.75rem",
    color: "#6c8aff",
    marginBottom: "4px",
    fontWeight: 600,
  },
  chatMsgContent: {
    whiteSpace: "pre-wrap" as const,
    lineHeight: 1.6,
  },
  chatInputArea: {
    display: "flex",
    gap: "12px",
    padding: "16px 24px",
    borderTop: "1px solid #1e1e2e",
  },
  chatTextarea: {
    flex: 1,
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "12px",
    padding: "12px 16px",
    color: "#e0e0e8",
    fontSize: "1rem",
    fontFamily: "inherit",
    resize: "none" as const,
    outline: "none",
  },
  chatSendBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "12px",
    padding: "12px 24px",
    fontSize: "1rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  chatStopBtn: {
    backgroundColor: "#ff4444",
    color: "#fff",
    border: "none",
    borderRadius: "12px",
    padding: "12px 16px",
    fontSize: "1rem",
    fontWeight: 600,
    cursor: "pointer",
  },
};
