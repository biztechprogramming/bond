"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";
import ConfirmDialog from "./components/ConfirmDialog";
import Pagination from "./components/Pagination";
import LoadingSkeleton from "./components/LoadingSkeleton";
import ErrorBanner from "./components/ErrorBanner";
import EmptyState from "./components/EmptyState";

const API = `${BACKEND_API}/optimization`;

interface Lesson {
  id: string;
  filename: string;
  title: string;
  content: string;
  status: string;
  first_observed: string;
  recurrences: number;
  correlated_low_score_turns: number;
  created_at: string;
  updated_at: string;
}

interface LessonsResponse {
  items: Lesson[];
  total: number;
  page: number;
  per_page: number;
  pages: number;
}

type Status = "all" | "proposed" | "approved" | "rejected";

export default function LessonsPanel() {
  const [status, setStatus] = useState<Status>("all");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [data, setData] = useState<LessonsResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  // Edit state
  const [editId, setEditId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState("");
  const [editContent, setEditContent] = useState("");
  const [dirty, setDirty] = useState<Set<string>>(new Set());

  // Confirm dialog
  const [confirm, setConfirm] = useState<{ id: string; action: string } | null>(null);

  const showToast = (msg: string) => { setToast(msg); setTimeout(() => setToast(""), 3000); };

  const fetchLessons = useCallback(async () => {
    try {
      setError("");
      const params = new URLSearchParams({ status, page: String(page), per_page: "50" });
      if (search) params.set("q", search);
      const res = await apiFetch(`${API}/lessons?${params}`);
      if (!res.ok) throw new Error("Failed to load lessons");
      setData(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load");
    } finally {
      setLoading(false);
    }
  }, [status, search, page]);

  useEffect(() => { setLoading(true); setPage(1); }, [status, search]);
  useEffect(() => { fetchLessons(); }, [fetchLessons]);

  const doAction = async (id: string, action: string, body?: Record<string, unknown>) => {
    try {
      const method = action === "edit" ? "PUT" : "POST";
      const url = action === "edit" ? `${API}/lessons/${id}` : `${API}/lessons/${id}/${action}`;
      const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined });
      if (!res.ok) { const d = await res.json().catch(() => ({})); throw new Error(d.detail || "Action failed"); }
      showToast(action === "approve" ? "Lesson approved" : action === "reject" ? "Lesson archived" : action === "revoke" ? "Lesson revoked" : "Lesson saved");
      setEditId(null);
      setDirty((prev) => { const n = new Set(prev); n.delete(id); return n; });
      fetchLessons();
    } catch (e: unknown) {
      showToast(e instanceof Error ? e.message : "Action failed");
    }
  };

  const handleConfirm = (textareaValue?: string) => {
    if (!confirm) return;
    if (confirm.action === "reject") doAction(confirm.id, "reject", textareaValue ? { reason: textareaValue } : undefined);
    else if (confirm.action === "revoke") doAction(confirm.id, "revoke");
    setConfirm(null);
  };

  if (loading) return <LoadingSkeleton lines={5} />;
  if (error) return <ErrorBanner message={error} onRetry={fetchLessons} />;

  const items = data?.items || [];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      {toast && <div style={{ padding: "8px 12px", backgroundColor: "#1a1a2e", borderRadius: "8px", color: "#6cffa0", fontSize: "0.85rem" }}>{toast}</div>}

      {/* Filters */}
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
        <select value={status} onChange={(e) => setStatus(e.target.value as Status)} style={selectStyle}>
          <option value="all">All</option>
          <option value="proposed">Proposed</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search lessons..."
          style={{ ...inputStyle, flex: 1, minWidth: "200px" }}
        />
      </div>

      {items.length === 0 ? (
        <EmptyState message="No lessons found" icon="📝" />
      ) : (
        items.map((lesson) => {
          const isEditing = editId === lesson.id;
          const isDirty = dirty.has(lesson.id);
          const statusIcon = lesson.status === "approved" ? "✅" : lesson.status === "rejected" ? "❌" : "⏳";

          return (
            <div key={lesson.id} style={{ ...cardStyle, borderColor: isDirty ? "#ffcc44" : "#1e1e2e" }}>
              {isEditing ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                  <input
                    value={editTitle}
                    onChange={(e) => { setEditTitle(e.target.value); setDirty((p) => new Set(p).add(lesson.id)); }}
                    style={inputStyle}
                    placeholder="Title"
                  />
                  <textarea
                    value={editContent}
                    onChange={(e) => { setEditContent(e.target.value); setDirty((p) => new Set(p).add(lesson.id)); }}
                    style={{ ...inputStyle, minHeight: "100px", resize: "vertical", fontFamily: "monospace" }}
                  />
                  <div style={{ display: "flex", gap: "8px" }}>
                    <button style={btnStyle} onClick={() => doAction(lesson.id, "edit", { title: editTitle, content: editContent })}>Save</button>
                    <button style={btnSecondaryStyle} onClick={() => { setEditId(null); setDirty((p) => { const n = new Set(p); n.delete(lesson.id); return n; }); }}>Cancel</button>
                  </div>
                </div>
              ) : (
                <>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "8px" }}>
                    <div>
                      <span style={{ marginRight: "6px" }}>{statusIcon}</span>
                      <strong style={{ color: "#e0e0e8" }}>{lesson.title}</strong>
                      {isDirty && <span style={{ color: "#ffcc44", marginLeft: "6px" }}>●</span>}
                    </div>
                    <div style={{ display: "flex", gap: "6px", flexShrink: 0 }}>
                      {lesson.status === "proposed" && (
                        <>
                          <button style={btnSmallStyle} onClick={() => doAction(lesson.id, "approve")}>Approve</button>
                          <button style={btnSmallStyle} onClick={() => { setEditId(lesson.id); setEditTitle(lesson.title); setEditContent(lesson.content); }}>Edit</button>
                          <button style={{ ...btnSmallStyle, color: "#ff6c8a" }} onClick={() => setConfirm({ id: lesson.id, action: "reject" })}>Reject</button>
                        </>
                      )}
                      {lesson.status === "approved" && (
                        <button style={btnSmallStyle} onClick={() => setConfirm({ id: lesson.id, action: "revoke" })}>Revoke</button>
                      )}
                    </div>
                  </div>
                  <div style={{ color: "#5a5a6e", fontSize: "0.8rem", marginTop: "4px" }}>
                    First seen: {lesson.first_observed} · Recurrences: {lesson.recurrences} · Correlated with {lesson.correlated_low_score_turns} low-scoring turns
                  </div>
                </>
              )}
            </div>
          );
        })
      )}

      {data && <Pagination page={data.page} totalPages={data.pages} onPageChange={setPage} />}

      <ConfirmDialog
        open={confirm?.action === "reject"}
        title="Reject this lesson?"
        message="It will be archived, not deleted."
        confirmLabel="Reject"
        onConfirm={handleConfirm}
        onCancel={() => setConfirm(null)}
        showTextarea
        textareaPlaceholder="Optional reason..."
      />
      <ConfirmDialog
        open={confirm?.action === "revoke"}
        title="Revoke this lesson?"
        message="It will be moved back to Proposed."
        confirmLabel="Revoke"
        onConfirm={handleConfirm}
        onCancel={() => setConfirm(null)}
      />
    </div>
  );
}

const cardStyle: React.CSSProperties = { backgroundColor: "#12121a", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", borderRadius: "12px", padding: "16px" };
const inputStyle: React.CSSProperties = { backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none", width: "100%", boxSizing: "border-box" };
const selectStyle: React.CSSProperties = { backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.85rem", outline: "none" };
const btnStyle: React.CSSProperties = { backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" };
const btnSecondaryStyle: React.CSSProperties = { backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" };
const btnSmallStyle: React.CSSProperties = { background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "6px", padding: "4px 10px", color: "#8888a0", fontSize: "0.8rem", cursor: "pointer" };
