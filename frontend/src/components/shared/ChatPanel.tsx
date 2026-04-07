import React, { useRef, useEffect } from "react";
import type { ChatMessage, AgentStatus } from "@/lib/types";
import { toolIcon } from "@/lib/theme";
import MarkdownMessage from "@/components/shared/MarkdownMessage";
import ImageGrid from "@/components/chat/ImageGrid";
import { extractImageResults, stripImageJson, rewriteImageSrc } from "@/lib/image-utils";

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
  /** Live stdout/stderr output from the coding agent */
  codingAgentOutput?: string[];
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
  codingAgentOutput = [],
  deleteMode = false,
  onDeleteMessage,
  onResendMessage,
  selectedAgentName,
}: ChatPanelProps) {
  const messagesEndRef = useRef<HTMLDivElement | null>(null);
  const [showToolLog, setShowToolLog] = React.useState(false);
  const [showAgentOutput, setShowAgentOutput] = React.useState(false);
  const [expandedFiles, setExpandedFiles] = React.useState<Set<string>>(new Set());
  const outputEndRef = useRef<HTMLDivElement | null>(null);
  const [copiedIdx, setCopiedIdx] = React.useState<number | null>(null);
  const [hoveredIdx, setHoveredIdx] = React.useState<number | null>(null);

  const copyMessage = (content: string, idx: number) => {
    const onSuccess = () => {
      setCopiedIdx(idx);
      setTimeout(() => setCopiedIdx(null), 1500);
    };

    // Prefer Clipboard API, fall back to execCommand for non-secure contexts / older mobile browsers
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(content).then(onSuccess).catch(() => {
        fallbackCopy(content) && onSuccess();
      });
    } else {
      fallbackCopy(content) && onSuccess();
    }
  };

  const fallbackCopy = (text: string): boolean => {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    textarea.style.top = "-9999px";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.focus();
    textarea.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    document.body.removeChild(textarea);
    return ok;
  };

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingContent]);

  // Auto-scroll agent output when new lines arrive (only if panel is open)
  useEffect(() => {
    if (showAgentOutput) {
      outputEndRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [codingAgentOutput, showAgentOutput]);

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
              ...(msg.role === "user" && !msg.content.startsWith("[System:") ? s.chatMsgUser : {}),
              ...(msg.role === "system" || (msg.role === "user" && msg.content.startsWith("[System:")) ? s.chatMsgSystem : {}),
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
                  background: copiedIdx === i ? "rgba(78,201,148,0.15)" : "rgba(90,90,110,0.2)",
                  borderWidth: 0, borderStyle: "none", borderColor: "transparent",
                  color: copiedIdx === i ? "#4ec994" : hoveredIdx === i ? "#a0a0b8" : "#6e6e85",
                  fontSize: "0.85rem",
                  cursor: "pointer",
                  padding: "4px 6px",
                  borderRadius: "6px",
                  transition: "color 0.15s, background 0.15s",
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
              {msg.role === "user" && msg.content.startsWith("[System:") ? "System" : msg.role === "user" ? "You" : msg.role === "assistant" ? (msg.agentName || selectedAgentName || "Agent") : "System"}
            </div>
            {msg.role === "assistant" ? (() => {
              const imageResult = extractImageResults(msg.content);
              if (imageResult) {
                const images = imageResult.paths.map((p) => ({
                  src: p,
                  prompt: imageResult.prompt,
                  revisedPrompt: imageResult.revisedPrompt,
                  provider: imageResult.provider,
                  model: imageResult.model,
                  size: imageResult.size,
                  cost: imageResult.cost,
                  onExpand: () => {},
                }));
                const textContent = stripImageJson(msg.content);
                return <div style={s.chatMsgContent}>
                  {textContent && <MarkdownMessage content={textContent} />}
                  <ImageGrid images={images} />
                </div>;
              }
              return <div style={s.chatMsgContent}><MarkdownMessage content={msg.content} /></div>;
            })() : (
              <div style={{...s.chatMsgContent, whiteSpace: "pre-wrap", ...(msg.role === "user" && msg.content.startsWith("[System:") ? { color: "#8888a0", fontSize: "0.85rem", fontStyle: "italic" } : {})}}>{msg.content}</div>
            )}
          </div>
        ))}
        {(loading || streamingContent) && (
          <div style={s.chatMsg}>
            <div style={s.chatMsgRole}>{selectedAgentName || currentAgentName}</div>
            <div style={{ ...s.chatMsgContent, color: streamingContent ? "#e0e0e8" : "#8888a0" }}>
              {streamingContent ? <MarkdownMessage content={streamingContent} /> : (
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
                          style={{ background: "none", borderWidth: 0, borderStyle: "none", borderColor: "transparent", color: "#5a5a6e", fontSize: "0.75rem", cursor: "pointer", padding: "4px 0", marginTop: "4px" }}
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
              padding: "8px 12px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
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
                        background: "none", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#0a0a14",
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
            {/* Live agent output log */}
            {codingAgentOutput.length > 0 && (
              <>
                <button
                  onClick={() => setShowAgentOutput((prev) => !prev)}
                  style={{
                    display: "flex", alignItems: "center", gap: "6px",
                    width: "100%", padding: "6px 12px",
                    background: "none", borderWidth: 0, borderStyle: "none", borderColor: "transparent",
                    borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#1e1e2e",
                    color: "#8888a0", fontSize: "0.75rem", cursor: "pointer",
                    textAlign: "left",
                  }}
                >
                  <span style={{ fontSize: "0.7rem" }}>
                    {showAgentOutput ? "▼" : "▶"}
                  </span>
                  <span>Agent Output</span>
                  <span style={{ color: "#5a5a6e", marginLeft: "auto", fontSize: "0.68rem" }}>
                    {codingAgentOutput.length} line{codingAgentOutput.length !== 1 ? "s" : ""}
                  </span>
                </button>
                {showAgentOutput && (
                  <div style={{
                    maxHeight: "300px", overflowY: "auto", overflowX: "auto",
                    padding: "8px 12px",
                    backgroundColor: "#08080d",
                    fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                    fontSize: "0.7rem", lineHeight: 1.4,
                    whiteSpace: "pre-wrap",
                    wordBreak: "break-all",
                    color: "#a0a0b8",
                  }}>
                    {codingAgentOutput.map((line, i) => (
                      <div key={i}>{line || "\u00A0"}</div>
                    ))}
                    <div ref={outputEndRef} />
                  </div>
                )}
              </>
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
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
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
    lineHeight: 1.5,
    fontSize: "0.85rem",
  },
  chatInputArea: {
    display: "flex",
    gap: "8px",
    padding: "10px 12px",
    borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#1e1e2e",
  },
  chatTextarea: {
    flex: 1,
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
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
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  chatStopBtn: {
    backgroundColor: "#ff4444",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  deleteActionBtn: {
    background: "rgba(108,138,255,0.15)", borderWidth: "1px", borderStyle: "solid", borderColor: "rgba(108,138,255,0.4)",
    borderRadius: "50%", width: "22px", height: "22px",
    color: "#6c8aff", fontSize: "0.75rem", cursor: "pointer",
    display: "flex", alignItems: "center", justifyContent: "center",
    lineHeight: 1, padding: 0,
  },
  deleteBtn: {
    background: "rgba(255,60,80,0.15)", borderWidth: "1px", borderStyle: "solid", borderColor: "rgba(255,60,80,0.4)",
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
    lineHeight: 1.6,
  },
  chatInputArea: {
    display: "flex",
    gap: "12px",
    padding: "16px 24px",
    borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#1e1e2e",
  },
  chatTextarea: {
    flex: 1,
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
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
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "12px",
    padding: "12px 24px",
    fontSize: "1rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  chatStopBtn: {
    backgroundColor: "#ff4444",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "12px",
    padding: "12px 16px",
    fontSize: "1rem",
    fontWeight: 600,
    cursor: "pointer",
  },
};
