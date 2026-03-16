import React, { useEffect, useRef, useState, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  environment: string;
  sources?: string[];
}

interface LogEntry {
  timestamp: string;
  source: string;
  severity: string;
  message: string;
  fingerprint?: string;
}

const SEVERITY_COLORS: Record<string, string> = {
  error: "#ff6c8a",
  warn: "#ffcc6c",
  warning: "#ffcc6c",
  info: "#e0e0e8",
  debug: "#5a5a70",
};

const SEVERITY_TABS = ["all", "errors", "warnings"] as const;
type SeverityFilter = typeof SEVERITY_TABS[number];

const TIME_RANGES = [
  { label: "15m", minutes: 15 },
  { label: "1h", minutes: 60 },
  { label: "6h", minutes: 360 },
  { label: "24h", minutes: 1440 },
];

function todayDate(): string {
  return new Date().toISOString().slice(0, 10);
}

function matchesFilter(severity: string, filter: SeverityFilter): boolean {
  if (filter === "all") return true;
  if (filter === "errors") return severity === "error";
  if (filter === "warnings") return severity === "warn" || severity === "warning";
  return true;
}

export default function LiveLogViewer({ environment, sources: availableSources }: Props) {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [offset, setOffset] = useState(0);
  const [source, setSource] = useState("all");
  const [search, setSearch] = useState("");
  const [severityFilter, setSeverityFilter] = useState<SeverityFilter>("all");
  const [timeRange, setTimeRange] = useState(60);
  const [liveTail, setLiveTail] = useState(true);
  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
  const [filingIssue, setFilingIssue] = useState(false);
  const [msg, setMsg] = useState("");
  const containerRef = useRef<HTMLDivElement>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchLogs = useCallback(async (reset?: boolean) => {
    try {
      const date = todayDate();
      const currentOffset = reset ? 0 : offset;
      const url = `${GATEWAY_API}/deployments/logs/${environment}/${date}?offset=${currentOffset}`;
      const res = await fetch(url);
      if (!res.ok) return;
      const data = await res.json();
      const entries: LogEntry[] = data.entries || data.logs || [];
      if (reset) {
        setLogs(entries);
      } else if (entries.length > 0) {
        setLogs((prev) => [...prev, ...entries]);
      }
      if (entries.length > 0) setOffset(data.next_offset ?? currentOffset + entries.length);
    } catch { /* ignore poll errors */ }
  }, [environment, offset]);

  useEffect(() => {
    setLogs([]);
    setOffset(0);
    fetchLogs(true);
  }, [environment]);

  useEffect(() => {
    if (liveTail) {
      pollRef.current = setInterval(() => fetchLogs(), 5000);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [liveTail, fetchLogs]);

  useEffect(() => {
    if (liveTail && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [logs, liveTail]);

  const handleFileIssue = async (entry: LogEntry) => {
    setFilingIssue(true);
    setMsg("");
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/issues`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ environment, fingerprint: entry.fingerprint, message: entry.message, source: entry.source, severity: entry.severity, timestamp: entry.timestamp }),
      });
      if (res.ok) setMsg("Issue filed.");
      else setMsg("Failed to file issue.");
    } catch (err: any) { setMsg(`Error: ${err.message}`); }
    setFilingIssue(false);
  };

  const cutoff = Date.now() - timeRange * 60 * 1000;
  const filteredLogs = logs.filter((l) => {
    if (!matchesFilter(l.severity, severityFilter)) return false;
    if (source !== "all" && l.source !== source) return false;
    if (search && !l.message.toLowerCase().includes(search.toLowerCase())) return false;
    if (new Date(l.timestamp).getTime() < cutoff) return false;
    return true;
  });

  const uniqueSources = Array.from(new Set(logs.map((l) => l.source).filter(Boolean)));
  const allSources = availableSources || uniqueSources;

  return (
    <div style={styles.container}>
      {/* Toolbar */}
      <div style={styles.toolbar}>
        <select style={styles.select} value={source} onChange={(e) => setSource(e.target.value)}>
          <option value="all">All Sources</option>
          {allSources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>
        <div style={{ display: "flex", gap: 2 }}>
          {TIME_RANGES.map((t) => (
            <button key={t.label} style={timeRange === t.minutes ? styles.activeTab : styles.tab} onClick={() => setTimeRange(t.minutes)}>{t.label}</button>
          ))}
        </div>
        <input style={styles.searchInput} placeholder="Search logs..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <label style={styles.checkLabel}>
          <input type="checkbox" checked={liveTail} onChange={(e) => setLiveTail(e.target.checked)} style={{ accentColor: "#6cffa0" }} />
          Live tail
        </label>
      </div>

      {/* Severity Tabs */}
      <div style={{ display: "flex", gap: 4 }}>
        {SEVERITY_TABS.map((t) => (
          <button key={t} style={severityFilter === t ? styles.activeTab : styles.tab} onClick={() => setSeverityFilter(t)}>
            {t.charAt(0).toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>

      {/* Log Area */}
      <div ref={containerRef} style={styles.logArea}>
        {filteredLogs.length === 0 && <div style={{ color: "#8888a0", fontSize: "0.8rem", padding: 12 }}>No logs matching filters.</div>}
        {filteredLogs.map((entry, i) => {
          const color = SEVERITY_COLORS[entry.severity] || SEVERITY_COLORS.info;
          const isError = entry.severity === "error";
          return (
            <div
              key={i}
              style={{ ...styles.logLine, color, cursor: isError ? "pointer" : "default", backgroundColor: selectedLog === entry ? "#1a1a3a" : "transparent" }}
              onClick={() => isError ? setSelectedLog(selectedLog === entry ? null : entry) : undefined}
            >
              <span style={styles.logTimestamp}>{new Date(entry.timestamp).toLocaleTimeString()}</span>
              <span style={styles.logSource}>{entry.source}</span>
              <span style={{ ...styles.logSeverity, color }}>{entry.severity.toUpperCase().slice(0, 4).padEnd(4)}</span>
              <span style={{ flex: 1 }}>{entry.message}</span>
            </div>
          );
        })}
      </div>

      {/* Error Detail Panel */}
      {selectedLog && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Error Detail</span>
          <div style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>{selectedLog.message}</div>
          {selectedLog.fingerprint && (
            <div style={{ fontSize: "0.75rem", color: "#8888a0" }}>Fingerprint: <code>{selectedLog.fingerprint}</code></div>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button style={styles.issueButton} onClick={() => handleFileIssue(selectedLog)} disabled={filingIssue}>
              {filingIssue ? "Filing..." : "File Issue"}
            </button>
            <button style={styles.secondaryButton} onClick={() => setSelectedLog(null)}>Dismiss</button>
          </div>
          {msg && <span style={{ fontSize: "0.8rem", color: msg.startsWith("Error") || msg.startsWith("Failed") ? "#ff6c8a" : "#6cffa0" }}>{msg}</span>}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 8 },
  toolbar: { display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" },
  select: { backgroundColor: "#16162a", color: "#e0e0e8", border: "1px solid #3a3a5a", borderRadius: 6, padding: "6px 10px", fontSize: "0.8rem" },
  searchInput: { backgroundColor: "#16162a", color: "#e0e0e8", border: "1px solid #3a3a5a", borderRadius: 6, padding: "6px 10px", fontSize: "0.8rem", flex: 1, minWidth: 150 },
  tab: { backgroundColor: "#12121a", color: "#8888a0", border: "1px solid #1e1e2e", borderRadius: 6, padding: "4px 10px", fontSize: "0.75rem", cursor: "pointer" },
  activeTab: { backgroundColor: "#2a2a4a", color: "#e0e0e8", border: "1px solid #6c8aff", borderRadius: 6, padding: "4px 10px", fontSize: "0.75rem", cursor: "pointer", fontWeight: 600 },
  checkLabel: { display: "flex", alignItems: "center", gap: 4, fontSize: "0.8rem", color: "#e0e0e8", cursor: "pointer" },
  logArea: {
    backgroundColor: "#0a0a12",
    border: "1px solid #1e1e2e",
    borderRadius: 8,
    fontFamily: "monospace",
    fontSize: "0.75rem",
    overflow: "auto",
    maxHeight: 500,
    minHeight: 200,
  },
  logLine: { display: "flex", gap: 8, padding: "3px 12px", borderBottom: "1px solid #12121a", whiteSpace: "nowrap" as const },
  logTimestamp: { color: "#5a5a70", width: 80, flexShrink: 0 },
  logSource: { color: "#6c8aff", width: 80, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis" },
  logSeverity: { width: 40, flexShrink: 0, fontWeight: 600 },
  card: { backgroundColor: "#12121a", border: "1px solid #1e1e2e", borderRadius: 12, padding: 16, display: "flex", flexDirection: "column", gap: 8 },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  issueButton: { backgroundColor: "#4a2a2a", color: "#ff6c8a", border: "1px solid #5a3a3a", borderRadius: 6, padding: "6px 12px", fontSize: "0.8rem", cursor: "pointer", fontWeight: 600 },
  secondaryButton: { backgroundColor: "#2a2a3e", color: "#e0e0e8", border: "1px solid #3a3a4e", borderRadius: 6, padding: "6px 12px", fontSize: "0.8rem", cursor: "pointer" },
};
