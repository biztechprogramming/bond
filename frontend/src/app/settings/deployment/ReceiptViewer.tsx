import React, { useEffect, useState } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

interface ReceiptPhase {
  name: string;
  status: string;
  duration_ms?: number;
  output?: string;
  stdout?: string;
  stderr?: string;
  message?: string;
}

interface Receipt {
  receipt_id: string;
  script_id: string;
  version: string;
  environment: string;
  status: string;
  started_at: string;
  completed_at?: string;
  duration_ms?: number;
  initiated_by?: string;
  previous_receipt_id?: string;
  phases?: ReceiptPhase[];
  validation?: ReceiptPhase;
  pre_hook?: ReceiptPhase;
  execution?: ReceiptPhase;
  post_hook?: ReceiptPhase;
  health_check?: ReceiptPhase;
}

interface Props {
  environment: string;
  receiptId?: string;
  scriptId?: string;
  onClose: () => void;
}

export default function ReceiptViewer({ environment, receiptId, scriptId, onClose }: Props) {
  const [receipt, setReceipt] = useState<Receipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  useEffect(() => {
    (async () => {
      setLoading(true);
      try {
        let url: string;
        if (receiptId) {
          url = `${GATEWAY_API}/deployments/receipts/${environment}/${receiptId}`;
        } else {
          // Fetch latest for environment
          const listRes = await apiFetch(`${GATEWAY_API}/deployments/receipts/${environment}?limit=1`);
          if (!listRes.ok) throw new Error("Failed to fetch receipts");
          const list = await listRes.json();
          const items = Array.isArray(list) ? list : [];
          const match = scriptId ? items.find((r: any) => r.script_id === scriptId) : items[0];
          if (!match) {
            setError("No receipt found");
            setLoading(false);
            return;
          }
          url = `${GATEWAY_API}/deployments/receipts/${environment}/${match.receipt_id || match.id}`;
        }
        const res = await fetch(url);
        if (!res.ok) throw new Error("Receipt not found");
        setReceipt(await res.json());
      } catch (err: any) {
        setError(err.message);
      }
      setLoading(false);
    })();
  }, [environment, receiptId, scriptId]);

  const togglePhase = (name: string) => {
    setExpanded((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const formatDuration = (ms?: number) => {
    if (!ms) return "";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
  };

  const statusIcon = (s: string) => {
    if (s === "success") return "\u2705";
    if (s === "failed") return "\u274c";
    if (s === "running" || s === "deploying") return "\u23f3";
    return "\u25cb";
  };

  const phases: ReceiptPhase[] = receipt?.phases || [
    receipt?.validation,
    receipt?.pre_hook,
    receipt?.execution,
    receipt?.post_hook,
    receipt?.health_check,
  ].filter(Boolean) as ReceiptPhase[];

  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.panel} onClick={(e) => e.stopPropagation()}>
        <div style={styles.header}>
          <span style={styles.title}>
            Receipt: {receipt?.script_id || scriptId || "..."} &rarr; {environment}
          </span>
          <button style={styles.closeBtn} onClick={onClose}>Close</button>
        </div>

        {loading && <div style={styles.muted}>Loading...</div>}
        {error && <div style={styles.error}>{error}</div>}

        {receipt && (
          <>
            <div style={styles.summaryRow}>
              <span>Status: {statusIcon(receipt.status)} {receipt.status}</span>
              {receipt.duration_ms != null && <span>Duration: {formatDuration(receipt.duration_ms)}</span>}
              {receipt.completed_at && (
                <span>{new Date(receipt.completed_at).toLocaleString()}</span>
              )}
            </div>

            {phases.length > 0 && (
              <div style={styles.phases}>
                {phases.map((phase, i) => (
                  <div key={phase.name || i} style={styles.phase}>
                    <div
                      style={styles.phaseHeader}
                      onClick={() => togglePhase(phase.name || String(i))}
                    >
                      <span>{expanded[phase.name || String(i)] ? "\u25be" : "\u25b8"}</span>
                      <span>{statusIcon(phase.status)} {phase.name}</span>
                      {phase.duration_ms != null && (
                        <span style={styles.phaseDuration}>({formatDuration(phase.duration_ms)})</span>
                      )}
                      {phase.message && <span style={styles.phaseMsg}> — {phase.message}</span>}
                    </div>
                    {expanded[phase.name || String(i)] && (
                      <div style={styles.phaseBody}>
                        {(phase.stdout || phase.output) && (
                          <pre style={styles.codeBlock}>{phase.stdout || phase.output}</pre>
                        )}
                        {phase.stderr && (
                          <pre style={{ ...styles.codeBlock, borderColor: "#4a2a2a" }}>{phase.stderr}</pre>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {(receipt.initiated_by || receipt.previous_receipt_id) && (
              <div style={styles.context}>
                {receipt.initiated_by && <span>Promoted by: {receipt.initiated_by}</span>}
                {receipt.previous_receipt_id && (
                  <span>Previous receipt: {receipt.previous_receipt_id}</span>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    backgroundColor: "rgba(0,0,0,0.6)",
    display: "flex",
    justifyContent: "flex-end",
    zIndex: 1000,
  },
  panel: {
    width: "min(600px, 90vw)",
    height: "100vh",
    backgroundColor: "#0e0e16",
    borderLeftWidth: "1px", borderLeftStyle: "solid", borderLeftColor: "#1e1e2e",
    padding: 20,
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: 16,
  },
  header: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  closeBtn: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: 6,
    padding: "6px 14px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  summaryRow: {
    display: "flex",
    gap: 16,
    fontSize: "0.85rem",
    color: "#e0e0e8",
    flexWrap: "wrap" as const,
  },
  phases: { display: "flex", flexDirection: "column", gap: 4 },
  phase: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 8,
  },
  phaseHeader: {
    padding: "10px 12px",
    display: "flex",
    gap: 8,
    alignItems: "center",
    cursor: "pointer",
    fontSize: "0.85rem",
    color: "#e0e0e8",
  },
  phaseDuration: { color: "#8888a0", fontSize: "0.8rem" },
  phaseMsg: { color: "#8888a0", fontSize: "0.8rem" },
  phaseBody: { padding: "0 12px 12px" },
  codeBlock: {
    backgroundColor: "#0a0a12",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: 6,
    padding: 10,
    fontSize: "0.8rem",
    color: "#e0e0e8",
    fontFamily: "monospace",
    whiteSpace: "pre-wrap" as const,
    overflowX: "auto" as const,
    margin: 0,
  },
  context: {
    fontSize: "0.8rem",
    color: "#8888a0",
    display: "flex",
    gap: 16,
    borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#1e1e2e",
    paddingTop: 12,
  },
  muted: { color: "#8888a0", fontSize: "0.85rem" },
  error: { color: "#ff6c8a", fontSize: "0.85rem" },
};
