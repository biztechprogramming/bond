"use client";

import { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

interface Backup {
  filename: string;
  tier: string;
  size_bytes: number;
  created_at: string;
  path: string;
}

interface Preview {
  module_name: string;
  conversations_count: number;
  messages_count: number;
  total_rows: number;
  oldest_date: string | null;
  newest_date: string | null;
  sample_conversations: { id: string; title: string | null; message_count: number; updated_at: string | null }[];
  tables: Record<string, number>;
}

interface RestoreDialogProps {
  onDismiss: () => void;
}

export default function RestoreDialog({ onDismiss }: RestoreDialogProps) {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedBackup, setSelectedBackup] = useState<Backup | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [result, setResult] = useState<{ total_restored: number; total_failed: number; tables: Record<string, { restored: number; failed: number }> } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`${GATEWAY_API}/backups`)
      .then((r) => r.json())
      .then((data) => {
        setBackups(data.backups || []);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, []);

  const handlePreview = async (backup: Backup) => {
    setSelectedBackup(backup);
    setPreview(null);
    setPreviewing(true);
    setError(null);
    try {
      const res = await fetch(`${GATEWAY_API}/backups/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: backup.path }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Preview failed");
      setPreview(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setPreviewing(false);
    }
  };

  const handleRestore = async () => {
    if (!selectedBackup) return;
    setRestoring(true);
    setError(null);
    try {
      const res = await fetch(`${GATEWAY_API}/backups/restore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selectedBackup.path }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || "Restore failed");
      setResult(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setRestoring(false);
    }
  };

  const handleDismiss = () => {
    sessionStorage.setItem("bond-restore-dismissed", "1");
    onDismiss();
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1048576) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1048576).toFixed(1)} MB`;
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString() + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  };

  const tierColor: Record<string, string> = {
    daily: "#6cffa0",
    weekly: "#6c8aff",
    monthly: "#ffa06c",
  };

  // Success state
  if (result) {
    return (
      <div style={styles.overlay}>
        <div style={styles.modal}>
          <h2 style={styles.heading}>Restore Complete</h2>
          <p style={{ color: "#6cffa0", marginBottom: "8px" }}>
            Restored {result.total_restored} rows across {Object.keys(result.tables || {}).length} tables.
            {result.total_failed > 0 && <span style={{ color: "#ffa06c" }}> ({result.total_failed} failed)</span>}
          </p>
          {result.tables && (
            <div style={{ fontSize: "0.8rem", color: "#8888a0", marginBottom: "12px" }}>
              {Object.entries(result.tables).map(([table, info]: [string, any]) => (
                <div key={table} style={{ display: "flex", justifyContent: "space-between", padding: "2px 0" }}>
                  <span>{table}</span>
                  <span>
                    <span style={{ color: "#6cffa0" }}>{info.restored}</span>
                    {info.failed > 0 && <span style={{ color: "#ff6c8a" }}> / {info.failed} failed</span>}
                  </span>
                </div>
              ))}
            </div>
          )}
          <p style={{ color: "#8888a0", fontSize: "0.85rem" }}>
            Data will appear automatically via your SpacetimeDB subscription.
          </p>
          <button style={styles.primaryBtn} onClick={onDismiss}>
            Done
          </button>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.overlay}>
      <div style={styles.modal}>
        <h2 style={styles.heading}>Restore Conversations</h2>
        <p style={{ color: "#8888a0", fontSize: "0.85rem", marginBottom: "16px" }}>
          No conversations found. Would you like to restore from a backup?
        </p>

        {error && (
          <div style={{ color: "#ff6c8a", fontSize: "0.85rem", marginBottom: "12px", padding: "8px 12px", backgroundColor: "#1a0a0f", borderRadius: "8px", border: "1px solid #ff6c8a33" }}>
            {error}
          </div>
        )}

        {loading ? (
          <p style={{ color: "#5a5a6e" }}>Loading backups...</p>
        ) : backups.length === 0 ? (
          <p style={{ color: "#5a5a6e" }}>No backups found.</p>
        ) : !selectedBackup ? (
          <div style={{ maxHeight: "300px", overflowY: "auto" }}>
            {backups.map((b) => (
              <div
                key={b.path}
                style={styles.backupItem}
                onClick={() => handlePreview(b)}
                onMouseEnter={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "#6c8aff")}
                onMouseLeave={(e) => ((e.currentTarget as HTMLElement).style.borderColor = "#1e1e2e")}
              >
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
                  <span style={{ ...styles.tierBadge, color: tierColor[b.tier] || "#e0e0e8", borderColor: tierColor[b.tier] || "#1e1e2e" }}>
                    {b.tier}
                  </span>
                  <span style={{ color: "#e0e0e8", fontSize: "0.85rem" }}>{formatDate(b.created_at)}</span>
                </div>
                <div style={{ color: "#5a5a6e", fontSize: "0.78rem" }}>{formatSize(b.size_bytes)}</div>
              </div>
            ))}
          </div>
        ) : (
          <div>
            <button
              style={{ ...styles.secondaryBtn, marginBottom: "12px", fontSize: "0.8rem" }}
              onClick={() => { setSelectedBackup(null); setPreview(null); }}
            >
              &larr; Back to list
            </button>
            <div style={{ color: "#e0e0e8", fontSize: "0.85rem", marginBottom: "8px" }}>
              <span style={{ ...styles.tierBadge, color: tierColor[selectedBackup.tier] || "#e0e0e8", borderColor: tierColor[selectedBackup.tier] || "#1e1e2e" }}>
                {selectedBackup.tier}
              </span>{" "}
              {formatDate(selectedBackup.created_at)} ({formatSize(selectedBackup.size_bytes)})
            </div>

            {previewing ? (
              <p style={{ color: "#5a5a6e" }}>Loading preview... (starting temp database, this may take a minute)</p>
            ) : preview ? (
              <div>
                <div style={{ fontSize: "0.72rem", color: "#5a5a6e", marginBottom: "8px" }}>
                  Source module: <span style={{ color: "#8888a0" }}>{preview.module_name}</span>
                </div>
                <div style={styles.previewStats}>
                  <div><span style={{ color: "#6c8aff" }}>{preview.total_rows}</span> total rows across <span style={{ color: "#6c8aff" }}>{Object.keys(preview.tables).length}</span> tables</div>
                  <div><span style={{ color: "#6c8aff" }}>{preview.conversations_count}</span> conversations &middot; <span style={{ color: "#6c8aff" }}>{preview.messages_count}</span> messages</div>
                  {preview.oldest_date && preview.newest_date && (
                    <div style={{ color: "#5a5a6e", fontSize: "0.78rem" }}>
                      {formatDate(preview.oldest_date)} &mdash; {formatDate(preview.newest_date)}
                    </div>
                  )}
                </div>
                {/* Table breakdown */}
                {Object.keys(preview.tables).length > 0 && (
                  <div style={{ marginTop: "10px", padding: "8px 12px", backgroundColor: "#0a0a0f", borderRadius: "8px", border: "1px solid #1e1e2e" }}>
                    {Object.entries(preview.tables).map(([table, count]) => (
                      <div key={table} style={{ display: "flex", justifyContent: "space-between", padding: "2px 0", fontSize: "0.78rem" }}>
                        <span style={{ color: "#8888a0" }}>{table}</span>
                        <span style={{ color: "#6c8aff" }}>{count as number}</span>
                      </div>
                    ))}
                  </div>
                )}
                {/* Conversation samples */}
                {preview.sample_conversations.length > 0 && (
                  <div style={{ marginTop: "12px", maxHeight: "150px", overflowY: "auto" }}>
                    {preview.sample_conversations.map((c) => (
                      <div key={c.id} style={{ padding: "6px 0", borderBottom: "1px solid #1e1e2e", fontSize: "0.82rem" }}>
                        <div style={{ color: "#e0e0e8" }}>{c.title || "Untitled"}</div>
                        <div style={{ color: "#5a5a6e", fontSize: "0.75rem" }}>{c.message_count} msgs{c.updated_at ? ` · ${formatDate(c.updated_at)}` : ""}</div>
                      </div>
                    ))}
                  </div>
                )}
                <button
                  style={{ ...styles.primaryBtn, marginTop: "16px", width: "100%" }}
                  onClick={handleRestore}
                  disabled={restoring}
                >
                  {restoring ? "Restoring..." : `Restore ${preview.total_rows} rows`}
                </button>
              </div>
            ) : null}
          </div>
        )}

        <button style={{ ...styles.secondaryBtn, marginTop: "16px", width: "100%" }} onClick={handleDismiss}>
          Start Fresh
        </button>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    backgroundColor: "rgba(0, 0, 0, 0.7)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "16px",
    padding: "28px",
    maxWidth: "480px",
    width: "90%",
    maxHeight: "80vh",
    overflowY: "auto",
  },
  heading: {
    color: "#e0e0e8",
    fontSize: "1.2rem",
    fontWeight: 700,
    margin: "0 0 8px 0",
  },
  backupItem: {
    padding: "12px",
    borderRadius: "8px",
    border: "1px solid #1e1e2e",
    backgroundColor: "#0a0a0f",
    marginBottom: "6px",
    cursor: "pointer",
    transition: "border-color 0.15s",
  },
  tierBadge: {
    fontSize: "0.7rem",
    fontWeight: 600,
    textTransform: "uppercase" as const,
    padding: "2px 6px",
    borderRadius: "4px",
    border: "1px solid",
  },
  previewStats: {
    padding: "12px",
    backgroundColor: "#0a0a0f",
    borderRadius: "8px",
    border: "1px solid #1e1e2e",
    display: "flex",
    flexDirection: "column" as const,
    gap: "4px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
  },
  primaryBtn: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  secondaryBtn: {
    backgroundColor: "transparent",
    color: "#8888a0",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    cursor: "pointer",
  },
};
