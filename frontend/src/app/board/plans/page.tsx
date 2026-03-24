"use client";

import { useEffect, useState, useMemo } from "react";
import { useWorkPlans } from "@/hooks/useSpacetimeDB";
import { getConnection, getWorkItems } from "@/lib/spacetimedb-client";

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
  const allPlans = useWorkPlans();
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [page, setPage] = useState(0);

  // Reset page when filters change
  useEffect(() => { setPage(0); }, [search, statusFilter]);

  // Client-side filtering
  const filteredPlans = useMemo(() => {
    let result = allPlans;
    if (statusFilter !== "all") {
      result = result.filter(p => p.status === statusFilter);
    }
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      result = result.filter(p => p.title.toLowerCase().includes(q));
    }
    return result;
  }, [allPlans, statusFilter, search]);

  // Client-side pagination
  const paginatedPlans = useMemo(() => {
    return filteredPlans.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  }, [filteredPlans, page]);

  const totalPages = Math.max(1, Math.ceil(filteredPlans.length / PAGE_SIZE));

  const itemCounts = (planId: string) => {
    const items = getWorkItems(planId);
    if (items.length === 0) return { done: 0, total: 0 };
    const done = items.filter(i => i.status === "done" || i.status === "complete" || i.status === "approved" || i.status === "tested").length;
    return { done, total: items.length };
  };

  const [ctrlShift, setCtrlShift] = useState(false);
  useEffect(() => {
    const down = (e: KeyboardEvent) => { if (e.ctrlKey && e.shiftKey) setCtrlShift(true); };
    const up = () => setCtrlShift(false);
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => { window.removeEventListener("keydown", down); window.removeEventListener("keyup", up); };
  }, []);

  const deletePlan = (planId: string, e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!ctrlShift && !confirm("Delete this plan?")) return;
    const conn = getConnection();
    if (conn) {
      conn.reducers.deleteWorkPlan({ id: planId });
    }
  };

  const formatDate = (timestamp: bigint) => {
    const d = new Date(Number(timestamp));
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  };

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

      {paginatedPlans.length === 0 ? (
        <div style={s.empty}>No plans found</div>
      ) : (
        <div style={s.list}>
          {paginatedPlans.map(plan => {
            const counts = itemCounts(plan.id);
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
                      <span>{plan.agentId}</span>
                      <span style={s.metaDot}>&middot;</span>
                      <span>{formatDate(plan.createdAt)}</span>
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
          <span style={s.pageInfo}>Page {page + 1} of {totalPages}</span>
          <button
            onClick={() => setPage(p => p + 1)}
            disabled={page >= totalPages - 1}
            style={{ ...s.pageBtn, opacity: page >= totalPages - 1 ? 0.4 : 1 }}
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "8px",
    padding: "10px 14px",
    color: "#e0e0e8",
    fontSize: "0.9rem",
    fontFamily: "inherit",
    outline: "none",
  },
  statusSelect: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
  },
  planRow: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "14px 16px",
    textDecoration: "none",
    color: "inherit",
    cursor: "pointer",
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
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
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
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
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    color: "#ff4444",
    fontSize: "1rem",
    cursor: "pointer",
    padding: "4px 8px",
    borderRadius: "4px",
    flexShrink: 0,
    lineHeight: 1,
  },
};
