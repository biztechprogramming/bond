"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";

const API = `${BACKEND_API}/skills`;

interface Skill {
  id: string;
  name: string;
  source: string;
  source_type: string;
  path: string;
  description: string;
  l0_summary: string;
  l1_overview: string;
  priority: number;
  pinned: number;
  excluded: number;
  updated_at: number;
  score: number;
  total_loads: number;
  total_uses: number;
  thumbs_up: number;
  thumbs_down: number;
  last_used: number | null;
}

interface SkillSource {
  source: string;
  source_type: string;
  count: number;
  last_sync: number;
}

interface UsageEntry {
  id: string;
  session_id: string;
  activated_at: number;
  loaded_at: number | null;
  references_read: number;
  scripts_run: number;
  task_completed: number;
  user_vote: string | null;
  voted_at: number | null;
  task_category: string | null;
}

type SortKey = "name" | "source" | "score" | "total_loads" | "total_uses" | "thumbs_up" | "thumbs_down";

const SOURCE_ICONS: Record<string, string> = {
  anthropics: "\u{1F9E0}",
  openai: "\u{1F916}",
  "vercel-labs": "\u25B2",
  superpowers: "\u26A1",
  local: "\u{1F4C1}",
};

function fmtDate(ts: number | null): string {
  if (!ts) return "\u2014";
  return new Date(ts * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function fmtScore(n: number): string {
  return (n * 100).toFixed(0);
}

export default function SkillsTab() {
  const [skills, setSkills] = useState<Skill[]>([]);
  const [sources, setSources] = useState<SkillSource[]>([]);
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("score");
  const [sortAsc, setSortAsc] = useState(false);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [usageMap, setUsageMap] = useState<Record<string, UsageEntry[]>>({});
  const [reindexing, setReindexing] = useState(false);
  const [msg, setMsg] = useState("");

  const fetchData = useCallback(async () => {
    try {
      const [skillsRes, sourcesRes] = await Promise.all([
        fetch(`${API}/`),
        fetch(`${API}/sources`),
      ]);
      setSkills(await skillsRes.json());
      setSources(await sourcesRes.json());
    } catch { /* API not available */ }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) setSortAsc(!sortAsc);
    else { setSortKey(key); setSortAsc(false); }
  };

  const filtered = skills
    .filter((sk) => {
      if (!search) return true;
      const q = search.toLowerCase();
      return sk.name.toLowerCase().includes(q) || sk.source.toLowerCase().includes(q) || (sk.description || "").toLowerCase().includes(q);
    })
    .sort((a, b) => {
      const av = a[sortKey] ?? 0;
      const bv = b[sortKey] ?? 0;
      if (typeof av === "string" && typeof bv === "string") return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number);
    });

  const toggleExpand = async (id: string) => {
    if (expandedId === id) { setExpandedId(null); return; }
    setExpandedId(id);
    if (!usageMap[id]) {
      try {
        const res = await fetch(`${API}/${encodeURIComponent(id)}/usage`);
        const data = await res.json();
        setUsageMap((prev) => ({ ...prev, [id]: data }));
      } catch { /* ignore */ }
    }
  };

  const pinSkill = async (skillId: string, pinned: boolean) => {
    await fetch(`${API}/pin`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skill_id: skillId, pinned }) });
    setSkills((prev) => prev.map((sk) => sk.id === skillId ? { ...sk, pinned: pinned ? 1 : 0 } : sk));
  };

  const excludeSkill = async (skillId: string, excluded: boolean) => {
    await fetch(`${API}/exclude`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ skill_id: skillId, excluded }) });
    setSkills((prev) => prev.map((sk) => sk.id === skillId ? { ...sk, excluded: excluded ? 1 : 0 } : sk));
  };

  const toggleSourceExclude = async (source: string, excluded: boolean) => {
    await fetch(`${API}/exclude-source`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ source, excluded }) });
    setSkills((prev) => prev.map((sk) => sk.source === source ? { ...sk, excluded: excluded ? 1 : 0 } : sk));
  };

  const reindex = async () => {
    setReindexing(true);
    setMsg("");
    try {
      const res = await fetch(`${API}/reindex`, { method: "POST" });
      const data = await res.json();
      if (data.error) setMsg(`Error: ${data.error}`);
      else { setMsg(`Reindexed ${data.indexed} skills`); await fetchData(); }
    } catch { setMsg("Reindex failed"); }
    setReindexing(false);
  };

  const totalBySource = skills.reduce<Record<string, number>>((acc, sk) => {
    acc[sk.source] = (acc[sk.source] || 0) + 1;
    return acc;
  }, {});

  const lastIndexed = skills.length > 0 ? Math.max(...skills.map((sk) => sk.updated_at)) : 0;

  const isSourceExcluded = (source: string) => skills.filter((sk) => sk.source === source).every((sk) => sk.excluded);

  const SortHeader = ({ label, k }: { label: string; k: SortKey }) => (
    <th
      style={st.th}
      onClick={() => toggleSort(k)}
    >
      {label} {sortKey === k ? (sortAsc ? "\u25B2" : "\u25BC") : ""}
    </th>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Header */}
      <section style={st.section}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
          <div>
            <h2 style={st.sectionTitle}>Skills Catalog</h2>
            <div style={{ color: "#8888a0", fontSize: "0.85rem" }}>
              {skills.length} skills indexed
              {Object.entries(totalBySource).map(([src, count]) => (
                <span key={src} style={{ marginLeft: 12 }}>{src}: {count}</span>
              ))}
            </div>
            {lastIndexed > 0 && (
              <div style={{ color: "#5a5a6e", fontSize: "0.78rem", marginTop: 4 }}>
                Last indexed: {fmtDate(lastIndexed)}
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {msg && <span style={{ color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0", fontSize: "0.85rem" }}>{msg}</span>}
            <button style={{ ...st.button, opacity: reindexing ? 0.5 : 1 }} onClick={reindex} disabled={reindexing}>
              {reindexing ? "Reindexing..." : "Reindex"}
            </button>
          </div>
        </div>

        {/* Source cards */}
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", marginBottom: 8 }}>
          {sources.map((src) => {
            const excl = isSourceExcluded(src.source);
            return (
              <div key={src.source} style={{
                backgroundColor: excl ? "#1a1a22" : "#1e1e2e",
                border: `1px solid ${excl ? "rgba(200, 100, 100, 0.3)" : "rgba(100, 100, 140, 0.3)"}`,
                borderRadius: 10,
                padding: "12px 16px",
                minWidth: 140,
                display: "flex",
                flexDirection: "column",
                gap: 6,
                opacity: excl ? 0.6 : 1,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span style={{ fontSize: 20 }}>{SOURCE_ICONS[src.source] || "\u{1F4E6}"}</span>
                  <label style={{ display: "flex", alignItems: "center", cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={!excl}
                      onChange={() => toggleSourceExclude(src.source, !excl)}
                      style={{ accentColor: "#6c8aff", width: 14, height: 14 }}
                    />
                  </label>
                </div>
                <div style={{ fontWeight: 600, color: "#e0e0e8", fontSize: "0.9rem" }}>{src.source}</div>
                <div style={{ color: "#8888a0", fontSize: "0.78rem" }}>{src.count} skills</div>
                <div style={{ color: "#5a5a6e", fontSize: "0.72rem" }}>Synced: {fmtDate(src.last_sync)}</div>
              </div>
            );
          })}
        </div>
      </section>

      {/* Skills table */}
      <section style={st.section}>
        <input
          type="text"
          placeholder="Search skills..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ ...st.input, marginBottom: 16, width: "100%" }}
        />

        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "0.85rem" }}>
            <thead>
              <tr>
                <SortHeader label="Name" k="name" />
                <SortHeader label="Source" k="source" />
                <SortHeader label="Score" k="score" />
                <SortHeader label="Loads" k="total_loads" />
                <SortHeader label="Uses" k="total_uses" />
                <SortHeader label="+1" k="thumbs_up" />
                <SortHeader label="-1" k="thumbs_down" />
                <th style={st.th}>Status</th>
                <th style={st.th}>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((sk) => (
                <React.Fragment key={sk.id}>
                  <tr
                    style={{
                      borderBottom: "1px solid rgba(100, 100, 140, 0.15)",
                      cursor: "pointer",
                      backgroundColor: expandedId === sk.id ? "rgba(100, 100, 140, 0.08)" : "transparent",
                    }}
                    onClick={() => toggleExpand(sk.id)}
                  >
                    <td style={st.td}>
                      <span style={{ color: "#e0e0e8", fontWeight: 500 }}>{sk.name}</span>
                    </td>
                    <td style={st.td}>
                      <span style={{ color: "#8888a0" }}>{SOURCE_ICONS[sk.source] || ""} {sk.source}</span>
                    </td>
                    <td style={st.td}>
                      <span style={{
                        color: sk.score >= 0.7 ? "#6cffa0" : sk.score >= 0.4 ? "#ffcc44" : "#ff6c8a",
                        fontWeight: 600,
                        fontVariantNumeric: "tabular-nums",
                      }}>
                        {fmtScore(sk.score)}
                      </span>
                    </td>
                    <td style={{ ...st.td, fontVariantNumeric: "tabular-nums" }}>{sk.total_loads}</td>
                    <td style={{ ...st.td, fontVariantNumeric: "tabular-nums" }}>{sk.total_uses}</td>
                    <td style={{ ...st.td, fontVariantNumeric: "tabular-nums", color: "#6cffa0" }}>{sk.thumbs_up}</td>
                    <td style={{ ...st.td, fontVariantNumeric: "tabular-nums", color: "#ff6c8a" }}>{sk.thumbs_down}</td>
                    <td style={st.td}>
                      {sk.excluded ? (
                        <span style={{ color: "#ff6c8a", fontSize: "0.8rem" }}>\u2716 Excluded</span>
                      ) : sk.pinned ? (
                        <span style={{ color: "#6c8aff", fontSize: "0.8rem" }}>\u{1F4CC} Pinned</span>
                      ) : (
                        <span style={{ color: "#6cffa0", fontSize: "0.8rem" }}>\u25CF Active</span>
                      )}
                    </td>
                    <td style={st.td} onClick={(e) => e.stopPropagation()}>
                      <div style={{ display: "flex", gap: 4 }}>
                        <button
                          style={st.actionBtn}
                          title={sk.pinned ? "Unpin" : "Pin"}
                          onClick={() => pinSkill(sk.id, !sk.pinned)}
                        >
                          {sk.pinned ? "\u{1F4CC}" : "\u{1F4CD}"}
                        </button>
                        <button
                          style={st.actionBtn}
                          title={sk.excluded ? "Include" : "Exclude"}
                          onClick={() => excludeSkill(sk.id, !sk.excluded)}
                        >
                          {sk.excluded ? "\u2705" : "\u{1F6AB}"}
                        </button>
                      </div>
                    </td>
                  </tr>
                  {expandedId === sk.id && (
                    <tr>
                      <td colSpan={9} style={{ padding: "12px 16px", backgroundColor: "rgba(100, 100, 140, 0.05)" }}>
                        {sk.description && (
                          <div style={{ color: "#c8c8e0", fontSize: "0.85rem", marginBottom: 8 }}>
                            <strong>Description:</strong> {sk.description}
                          </div>
                        )}
                        {sk.l1_overview && (
                          <div style={{ color: "#a0a0b8", fontSize: "0.82rem", marginBottom: 8, whiteSpace: "pre-wrap" }}>
                            <strong>Overview:</strong> {sk.l1_overview}
                          </div>
                        )}
                        <div style={{ color: "#5a5a6e", fontSize: "0.78rem", marginBottom: 8 }}>
                          Path: {sk.path} | Priority: {sk.priority} | Last used: {fmtDate(sk.last_used)}
                        </div>
                        {usageMap[sk.id] && usageMap[sk.id].length > 0 && (
                          <div style={{ marginTop: 8 }}>
                            <div style={{ color: "#8888a0", fontSize: "0.78rem", fontWeight: 600, marginBottom: 4 }}>Recent activations:</div>
                            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                              {usageMap[sk.id].slice(0, 5).map((u) => (
                                <div key={u.id} style={{ color: "#5a5a6e", fontSize: "0.75rem" }}>
                                  {fmtDate(u.activated_at)}
                                  {u.user_vote && <span style={{ marginLeft: 8, color: u.user_vote === "up" ? "#6cffa0" : "#ff6c8a" }}>{u.user_vote === "up" ? "\u{1F44D}" : "\u{1F44E}"}</span>}
                                  {u.task_category && <span style={{ marginLeft: 8 }}>[{u.task_category}]</span>}
                                </div>
                              ))}
                            </div>
                          </div>
                        )}
                        {usageMap[sk.id] && usageMap[sk.id].length === 0 && (
                          <div style={{ color: "#5a5a6e", fontSize: "0.78rem" }}>No usage history yet.</div>
                        )}
                      </td>
                    </tr>
                  )}
                </React.Fragment>
              ))}
              {filtered.length === 0 && (
                <tr>
                  <td colSpan={9} style={{ padding: 24, textAlign: "center", color: "#5a5a6e" }}>
                    {skills.length === 0 ? "No skills indexed. Click Reindex to scan skill sources." : "No skills match your search."}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

const st: Record<string, React.CSSProperties> = {
  section: { backgroundColor: "#12121a", borderRadius: 12, padding: 24, border: "1px solid #1e1e2e" },
  sectionTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 4px 0" },
  button: { backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: 8, padding: "8px 16px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" },
  input: { backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: 8, padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none" },
  th: { textAlign: "left" as const, padding: "8px 12px", color: "#8888a0", fontSize: "0.78rem", fontWeight: 600, cursor: "pointer", userSelect: "none" as const, borderBottom: "1px solid rgba(100, 100, 140, 0.3)", whiteSpace: "nowrap" as const },
  td: { padding: "10px 12px", color: "#c8c8e0", whiteSpace: "nowrap" as const },
  actionBtn: { background: "none", border: "1px solid rgba(100, 100, 140, 0.3)", borderRadius: 6, padding: "3px 6px", cursor: "pointer", fontSize: 14, lineHeight: "1" },
};
