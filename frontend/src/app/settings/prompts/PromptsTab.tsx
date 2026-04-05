"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";

const API = `${BACKEND_API}/prompts`;

interface Fragment {
  path: string;
  tier: number;
  phase: string | null;
  utterances: string[];
  token_estimate: number;
}

interface Template {
  id: string;
  name: string;
  display_name: string;
  category: string;
  content: string;
  variables: string[];
  description: string;
  is_active: number;
  version: number;
}

interface Version {
  id: string;
  version: number;
  content: string;
  change_reason: string;
  changed_by: string;
  created_at: string;
}

type SubTab = "fragments" | "templates";

const TIER_COLORS: Record<number, string> = {
  1: "#6cffa0",
  2: "#6c8aff",
  3: "#ffcc44",
};

const TIER_LABELS: Record<number, string> = {
  1: "Always-on",
  2: "Lifecycle",
  3: "Semantic",
};

export default function PromptsTab() {
  const [subTab, setSubTab] = useState<SubTab>("fragments");
  const [fragments, setFragments] = useState<Fragment[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [editingFragment, setEditingFragment] = useState<string | null>(null); // path of fragment being edited
  const [fragmentContent, setFragmentContent] = useState("");
  const [fragmentLoading, setFragmentLoading] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState<Template | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [showVersions, setShowVersions] = useState<string | null>(null);
  const [msg, setMsg] = useState("");

  // AI generation state
  const [showAiGen, setShowAiGen] = useState(false);
  const [aiRole, setAiRole] = useState("");
  const [aiTools, setAiTools] = useState("");
  const [aiResponsibilities, setAiResponsibilities] = useState("");
  const [aiResult, setAiResult] = useState("");
  const [aiGenerating, setAiGenerating] = useState(false);

  const fetchAll = useCallback(async () => {
    try {
      const [fRes, tRes] = await Promise.all([
        apiFetch(`${API}/fragments`),
        apiFetch(`${API}/templates`),
      ]);
      setFragments(await fRes.json());
      setTemplates(await tRes.json());
    } catch { /* API not available */ }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const showMsg = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 3000); };

  const openFragmentEditor = async (path: string) => {
    setFragmentLoading(true);
    try {
      const res = await apiFetch(`${API}/fragments/${path}`);
      if (res.ok) {
        const data = await res.json();
        setFragmentContent(data.content);
        setEditingFragment(path);
      } else showMsg("Failed to load fragment");
    } catch { showMsg("Failed to load fragment"); }
    setFragmentLoading(false);
  };

  const saveFragment = async () => {
    if (!editingFragment) return;
    try {
      const res = await apiFetch(`${API}/fragments/${editingFragment}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: fragmentContent }),
      });
      if (res.ok) {
        showMsg("Fragment saved");
        setEditingFragment(null);
        await fetchAll();
      } else showMsg("Failed to save");
    } catch { showMsg("Failed to save"); }
  };

  const loadVersions = async (type: "fragments" | "templates", id: string) => {
    const res = await apiFetch(`${API}/${type}/${id}/versions`);
    if (res.ok) { setVersions(await res.json()); setShowVersions(id); }
  };

  const rollback = async (type: "fragments" | "templates", id: string, version: number) => {
    const res = await apiFetch(`${API}/${type}/${id}/rollback/${version}`, { method: "POST" });
    if (res.ok) { showMsg(`Rolled back to v${version}`); await fetchAll(); loadVersions(type, id); }
  };

  const generateSystemPrompt = async () => {
    setAiGenerating(true);
    try {
      const res = await apiFetch(`${API}/generate/system-prompt`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          agent_name: "Agent",
          agent_role: aiRole,
          tools: aiTools,
          responsibilities: aiResponsibilities,
        }),
      });
      const data = await res.json();
      if (res.ok) setAiResult(data.generated_prompt);
      else showMsg("Generation failed");
    } catch { showMsg("Generation failed"); }
    setAiGenerating(false);
  };

  const saveTemplate = async (tmpl: Template, changeReason: string) => {
    const res = await apiFetch(`${API}/templates/${tmpl.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: tmpl.display_name,
        content: tmpl.content,
        description: tmpl.description,
        is_active: !!tmpl.is_active,
        change_reason: changeReason,
      }),
    });
    if (res.ok) { showMsg("Template saved"); setEditingTemplate(null); await fetchAll(); }
    else showMsg("Failed to save");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {msg && <div style={{ ...s.msg, color: msg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}

      {/* Sub-tabs */}
      <div style={s.subTabBar}>
        <button style={subTab === "fragments" ? { ...s.subTab, ...s.subTabActive } : s.subTab} onClick={() => setSubTab("fragments")}>
          Fragments ({fragments.length})
        </button>
        <button style={subTab === "templates" ? { ...s.subTab, ...s.subTabActive } : s.subTab} onClick={() => setSubTab("templates")}>
          Templates ({templates.length})
        </button>
      </div>

      {/* ═══ FRAGMENTS TAB ═══ */}
      {subTab === "fragments" && (
        <div>
          {/* Info banner */}
          <div style={{ ...s.section, marginBottom: "12px", display: "flex", alignItems: "center", gap: "10px" }}>
            <span style={{ fontSize: "1.1rem" }}>📁</span>
            <span style={{ color: "#8888a0", fontSize: "0.85rem" }}>
              Fragments are managed as files in <code style={{ color: "#6c8aff" }}>prompts/</code> and versioned in git.
              Tier 1 = always-on, Tier 2 = lifecycle-triggered, Tier 3 = semantic router.
            </span>
          </div>

          {/* System prompt generator */}
          <div style={s.section}>
            <button style={{ ...s.btnSecondary, marginBottom: showAiGen ? "12px" : 0 }} onClick={() => setShowAiGen(!showAiGen)}>
              🤖 {showAiGen ? "Hide" : "Show"} System Prompt Generator
            </button>
            {showAiGen && (
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                <input style={s.input} placeholder="Agent role (e.g. 'Senior developer focused on Python backends')" value={aiRole} onChange={(e) => setAiRole(e.target.value)} />
                <input style={s.input} placeholder="Available tools (e.g. 'file_read, file_write, code_execute, git')" value={aiTools} onChange={(e) => setAiTools(e.target.value)} />
                <input style={s.input} placeholder="Key responsibilities (e.g. 'Implement features, write tests, review PRs')" value={aiResponsibilities} onChange={(e) => setAiResponsibilities(e.target.value)} />
                <button style={{ ...s.btn, opacity: aiGenerating ? 0.5 : 1 }} onClick={generateSystemPrompt} disabled={aiGenerating}>
                  {aiGenerating ? "🤖 Generating..." : "🤖 Generate System Prompt"}
                </button>
                {aiResult && (
                  <div style={{ position: "relative" }}>
                    <textarea style={{ ...s.textarea, height: "300px" }} value={aiResult} onChange={(e) => setAiResult(e.target.value)} />
                    <button style={{ ...s.btnSmall, position: "absolute", top: "8px", right: "8px" }} onClick={() => { navigator.clipboard.writeText(aiResult); showMsg("Copied!"); }}>
                      📋 Copy
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Fragment list */}
          {fragments.map((f, i) => {
            const name = f.path.split("/").pop()?.replace(".md", "") || f.path;
            const category = f.path.split("/")[0];
            const isEditing = editingFragment === f.path;
            return (
              <div key={f.path ?? `frag-${i}`} style={s.card}>
                <div style={s.cardHeader}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span style={{ ...s.badge, backgroundColor: TIER_COLORS[f.tier] || "#888" }}>Tier {f.tier}</span>
                    <strong style={{ color: "#e0e0e8" }}>{name}</strong>
                    <span style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>{category}</span>
                  </div>
                  <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                    <span style={{ ...s.badge, backgroundColor: "#2a2a3e", color: "#8888a0" }}>{TIER_LABELS[f.tier] || `Tier ${f.tier}`}</span>
                    {!isEditing && (
                      <button style={s.btnSmall} onClick={() => openFragmentEditor(f.path)} disabled={fragmentLoading}>
                        {fragmentLoading && editingFragment === null ? "..." : "Edit"}
                      </button>
                    )}
                  </div>
                </div>
                <div style={{ color: "#5a5a6e", fontSize: "0.8rem", fontFamily: "monospace", margin: "4px 0" }}>{f.path}</div>
                <div style={s.cardMeta}>
                  ~{f.token_estimate} tokens
                  {f.phase && <> · phase: {f.phase}</>}
                  {f.utterances.length > 0 && <> · triggers: {f.utterances.join(", ")}</>}
                </div>
                {isEditing && (
                  <div style={{ marginTop: "10px", display: "flex", flexDirection: "column", gap: "8px" }}>
                    <textarea
                      style={{ ...s.textarea, height: "300px" }}
                      value={fragmentContent}
                      onChange={(e) => setFragmentContent(e.target.value)}
                    />
                    <div style={{ display: "flex", gap: "8px" }}>
                      <button style={s.btn} onClick={saveFragment}>Save</button>
                      <button style={s.btnSecondary} onClick={() => setEditingFragment(null)}>Cancel</button>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* ═══ TEMPLATES TAB ═══ */}
      {subTab === "templates" && (
        <div>
          {templates.map((t, ti) => (
            <div key={t.id ?? `tmpl-${ti}`} style={s.card}>
              {editingTemplate?.id === t.id ? (
                <TemplateEditor template={editingTemplate} onSave={(upd) => saveTemplate(upd, "Updated from UI")} onCancel={() => setEditingTemplate(null)} />
              ) : (
                <>
                  <div style={s.cardHeader}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ ...s.badge, backgroundColor: "#6c8aff" }}>{t.category}</span>
                      <strong style={{ color: "#e0e0e8" }}>{t.display_name}</strong>
                      <span style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>v{t.version}</span>
                    </div>
                    <div style={{ display: "flex", gap: "6px" }}>
                      <button style={s.btnSmall} onClick={() => setEditingTemplate({ ...t })}>Edit</button>
                      <button style={s.btnSmall} onClick={() => loadVersions("templates", t.id)}>History</button>
                    </div>
                  </div>
                  <p style={{ color: "#8888a0", fontSize: "0.85rem", margin: "4px 0" }}>{t.description}</p>
                  <div style={s.cardMeta}>
                    Variables: {t.variables.join(", ")}
                  </div>
                  <pre style={s.pre}>{t.content.substring(0, 300)}{t.content.length > 300 ? "..." : ""}</pre>
                </>
              )}

              {showVersions === t.id && (
                <div style={s.versionPanel}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                    <strong style={{ color: "#e0e0e8" }}>Version History</strong>
                    <button style={s.btnSmall} onClick={() => setShowVersions(null)}>Close</button>
                  </div>
                  {versions.map((v, vi) => (
                    <div key={v.id ?? `ver-${vi}`} style={s.versionRow}>
                      <span style={{ color: "#6c8aff" }}>v{v.version}</span>
                      <span style={{ color: "#8888a0", flex: 1 }}>{v.change_reason}</span>
                      <span style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>{v.changed_by} · {new Date(v.created_at).toLocaleDateString()}</span>
                      <button style={s.btnSmall} onClick={() => rollback("templates", t.id, v.version)}>Restore</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Template Editor ──

function TemplateEditor({ template, onSave, onCancel }: {
  template: Template;
  onSave: (t: Template) => void;
  onCancel: () => void;
}) {
  const [t, setT] = useState({ ...template });

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      <input style={s.input} placeholder="Display Name" value={t.display_name} onChange={(e) => setT({ ...t, display_name: e.target.value })} />
      <input style={s.input} placeholder="Description" value={t.description} onChange={(e) => setT({ ...t, description: e.target.value })} />
      <textarea style={{ ...s.textarea, height: "350px" }} value={t.content} onChange={(e) => setT({ ...t, content: e.target.value })} />
      <div style={{ display: "flex", gap: "8px" }}>
        <button style={s.btn} onClick={() => onSave(t)}>Save</button>
        <button style={s.btnSecondary} onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}

// ── Styles ──

const s: Record<string, React.CSSProperties> = {
  subTabBar: { display: "flex", gap: "4px" },
  subTab: {
    background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px",
    color: "#8888a0", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer",
  },
  subTabActive: { color: "#6c8aff", borderColor: "#6c8aff", backgroundColor: "#1a1a2e" },
  genBar: { display: "flex", gap: "8px", alignItems: "center" },
  section: { backgroundColor: "#12121a", borderRadius: "12px", padding: "16px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },
  card: { backgroundColor: "#12121a", borderRadius: "12px", padding: "16px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  cardMeta: { color: "#5a5a6e", fontSize: "0.8rem", marginBottom: "8px" },
  badge: { fontSize: "0.7rem", padding: "2px 8px", borderRadius: "4px", color: "#000", fontWeight: 600, textTransform: "uppercase" as const },
  pre: { backgroundColor: "#0a0a14", borderRadius: "8px", padding: "12px", color: "#8888a0", fontSize: "0.8rem", margin: 0, whiteSpace: "pre-wrap" as const, overflow: "hidden", maxHeight: "100px" },
  versionPanel: { marginTop: "12px", padding: "12px", backgroundColor: "#0a0a14", borderRadius: "8px" },
  versionRow: { display: "flex", gap: "12px", alignItems: "center", padding: "6px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" },
  input: { backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none" },
  select: { backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none" },
  textarea: { backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "12px", color: "#e0e0e8", fontSize: "0.85rem", outline: "none", fontFamily: "monospace", resize: "vertical" as const, width: "100%", boxSizing: "border-box" as const },
  btn: { backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "10px 20px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" },
  btnSecondary: { backgroundColor: "#2a2a3e", color: "#e0e0e8", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "10px 20px", fontSize: "0.85rem", cursor: "pointer" },
  btnSmall: { background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "6px", padding: "4px 10px", color: "#8888a0", fontSize: "0.8rem", cursor: "pointer" },
  msg: { fontSize: "0.85rem", padding: "8px 12px", borderRadius: "8px", backgroundColor: "#1a1a2e" },
};
