"use client";

import { useEffect, useState, useCallback } from "react";

interface WorkItem {
  id: string;
  status: string;
}

interface WorkPlan {
  id: string;
  agent_id: string;
  title: string;
  status: string;
  created_at: string;
  items?: WorkItem[];
}

const API_BASE = "http://localhost:18790/api/v1";

const STATUS_EMOJI: Record<string, string> = {
  active: "\uD83D\uDD04",
  paused: "\u23F8",
  completed: "\u2705",
  failed: "\u274C",
  cancelled: "\uD83D\uDEAB",
};

const STATUS_OPTIONS = ["all", "active", "paused", "completed", "failed", "cancelled"];
const PAGE_SIZE = 20;

export default function AllPlansPage() {
  const [plans, setPlans] = useState<WorkPlan[]>([]);
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);

  const fetchPlans = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(page * PAGE_SIZE),
      });
      if (statusFilter !== "all") params.set("status", statusFilter);
      if (search.trim()) params.set("search", search.trim());

      const res = await fetch(`${API_BASE}/plans?${params}`);
      if (!res.ok) return;
      const data: WorkPlan[] = await res.json();

      // Fetch item details for each plan to get counts
      const withItems = await Promise.all(
        data.map(async (plan) => {
          try {
            const r = await fetch(`${API_BASE}/plans/${plan.id}`);
            if (r.ok) {
              const detail: WorkPlan = await r.json();
              return detail;
            }
          } catch { /* ignore */ }
          return plan;
        })
      );

      setPlans(withItems);
      // If we got a full page, there might be more
      setTotal(data.length === PAGE_SIZE ? (page + 2) * PAGE_SIZE : page * PAGE_SIZE + data.length);
    } catch { /* API not available */ }
  }, [page, statusFilter, search]);

  useEffect(() => { fetchPlans(); }, [fetchPlans]);

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [search, statusFilter]);

  const itemCounts = (plan: WorkPlan) => {
    if (!plan.items || plan.items.length === 0) return { done: 0, total: 0 };
    const done = plan.items.filter(i => i.status === "done" || i.status === "complete" || i.status === "approved" || i.status === "tested").length;
    return { done, total: plan.items.length };
  };

  const [ctrlShift, setCtrlShift] = useState(false);
  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.ctrlKey && e.shiftKey) setCtrlShift(true); };
    const up = () => setCtrlShift(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, []);

  const deletePlan = async (planId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!ctrlShift && !confirm("Delete this plan?")) return;
    try {
      await fetch(`${API_BASE}/plans/${planId}`, { method: "DELETE" });
      setPlans(prev => prev.filter(p => p.id !== planId));
    } catch { /* ignore */ }
  };

  const formatDate = (iso: string) => {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  };

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div style={s.container}>
      <header style={s.header}>
        <a href="/board" style={s.backLink}>&larr; Board</a>
        <h1 style={s.title}>All Plans</h1>
      </header>

      <div style={s.filters}>
        <input
          type="text"
          placeholder="Search plans..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={s.searchInput}
        />
        <select
          value={statusFilter}
          onChange={e => setStatusFilter(e.target.value)}
          style={s.statusSelect}
        >
          {STATUS_OPTIONS.map(opt => (
            <option key={opt} value={opt}>
              {opt === "all" ? "All statuses" : `${STATUS_EMOJI[opt] || ""} ${opt.charAt(0).toUpperCase() + opt.slice(1)}`}
            </option>
          ))}
        </select>
      </div>

      {plans.length === 0 ? (
        <div style={s.empty}>No plans found</div>
      ) : (
        <div style={s.list}>
          {plans.map(plan => {
            const counts = itemCounts(plan);
            return (
              <a
                key={plan.id}
                href={`/board?plan=${plan.id}`}
                style={s.planRow}
                onMouseEnter={e => (e.currentTarget.style.backgroundColor = "#1e1e2e")}
                onMouseLeave={e => (e.currentTarget.style.backgroundColor = "transparent")}
              >
                <div style={s.planMain}>
                  <span style={s.planEmoji}>{STATUS_EMOJI[plan.status] || "\uD83D\uDCCB"}</span>
                  <div style={s.planInfo}>
                    <div style={s.planTitle}>{plan.title}</div>
                    <div style={s.planMeta}>
                      <span>{plan.agent_id}</span>
                      <span style={s.metaDot}>&middot;</span>
                      <span>{formatDate(plan.created_at)}</span>
                      {counts.total > 0 && (
                        <>
                          <span style={s.metaDot}>&middot;</span>
                          <span>{counts.done}/{counts.total} done</span>
                        </>
                      )}
                    </div>
                  </div>
                </div>
                <button
                  onClick={(e) => deletePlan(plan.id, e)}
                  style={ctrlShift ? s.deleteBtnDanger : s.deleteBtn}
                  title={ctrlShift ? "Delete immediately" : "Delete plan"}
                >
                  ✕
                </button>
                <span style={s.arrow}>&rsaquo;</span>
              </a>
            );
          })}
        </div>
      )}

      {totalPages > 1 && (
        <div style={s.pagination}>
          <button
            onClick={() => setPage(p => Math.max(0, p - 1))}
            disabled={page === 0}
            style={{ ...s.pageBtn, opacity: page === 0 ? 0.4 : 1 }}
          >
            &larr; Prev
          </button>
          <span style={s.pageInfo}>Page {page + 1}</span>
          <button
            onClick={() => setPage(p => p + 1)}
            disabled={plans.length < PAGE_SIZE}
            style={{ ...s.pageBtn, opacity: plans.length < PAGE_SIZE ? 0.4 : 1 }}
          >
            Next &rarr;
          </button>
        </div>
      )}
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    minHeight: "100vh",
    maxWidth: "800px",
    margin: "0 auto",
    padding: "20px",
  },
  header: {
    display: "flex",
    alignItems: "center",
    gap: "16px",
    marginBottom: "24px",
  },
  backLink: {
    color: "#6c8aff",
    textDecoration: "none",
    fontSize: "0.9rem",
    padding: "6px 12px",
    borderRadius: "8px",
    border: "1px solid #2a2a3e",
  },
  title: {
    fontSize: "1.3rem",
    fontWeight: 600,
    color: "#e0e0e8",
    margin: 0,
  },
  filters: {
    display: "flex",
    gap: "12px",
    marginBottom: "20px",
    flexWrap: "wrap" as const,
  },
  searchInput: {
    flex: 1,
    minWidth: "200px",
    backgroundColor: "#12121a",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "10px 14px",
    color: "#e0e0e8",
    fontSize: "0.9rem",
    fontFamily: "inherit",
    outline: "none",
  },
  statusSelect: {
    backgroundColor: "#12121a",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "10px 14px",
    color: "#e0e0e8",
    fontSize: "0.9rem",
    fontFamily: "inherit",
    outline: "none",
    cursor: "pointer",
  },
  empty: {
    textAlign: "center" as const,
    color: "#5a5a6e",
    padding: "60px 20px",
    fontSize: "0.95rem",
  },
  list: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "2px",
    borderRadius: "10px",
    overflow: "hidden",
    border: "1px solid #1e1e2e",
  },
  planRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "14px 16px",
    textDecoration: "none",
    color: "inherit",
    cursor: "pointer",
    borderBottom: "1px solid #1e1e2e",
    transition: "background-color 0.15s",
  },
  planMain: {
    display: "flex",
    alignItems: "center",
    gap: "12px",
    flex: 1,
    minWidth: 0,
  },
  planEmoji: {
    fontSize: "1.2rem",
    flexShrink: 0,
  },
  planInfo: {
    flex: 1,
    minWidth: 0,
  },
  planTitle: {
    fontSize: "0.9rem",
    color: "#e0e0e8",
    fontWeight: 500,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap" as const,
  },
  planMeta: {
    display: "flex",
    gap: "6px",
    fontSize: "0.75rem",
    color: "#5a5a6e",
    marginTop: "4px",
    flexWrap: "wrap" as const,
  },
  metaDot: {
    color: "#3a3a4e",
  },
  arrow: {
    fontSize: "1.2rem",
    color: "#3a3a4e",
    flexShrink: 0,
    marginLeft: "8px",
  },
  pagination: {
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "16px",
    marginTop: "24px",
    padding: "12px 0",
  },
  pageBtn: {
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "8px 16px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    cursor: "pointer",
    fontFamily: "inherit",
  },
  pageInfo: {
    fontSize: "0.85rem",
    color: "#8888a0",
  },
  deleteBtn: {
    background: "none",
    border: "none",
    color: "#5a5a6e",
    fontSize: "1rem",
    cursor: "pointer",
    padding: "4px 8px",
    borderRadius: "4px",
    flexShrink: 0,
    lineHeight: 1,
  },
  deleteBtnDanger: {
    background: "none",
    border: "none",
    color: "#ff4444",
    fontSize: "1rem",
    cursor: "pointer",
    padding: "4px 8px",
    borderRadius: "4px",
    flexShrink: 0,
    lineHeight: 1,
  },
};
