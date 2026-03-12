"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API } from "@/lib/config";

const API = `${BACKEND_API}/prompts`;

interface Fragment {
  id: string;
  name: string;
  display_name: string;
  category: string;
  content: string;
  description: string;
  is_active: number;
  is_system: number;
  agent_count: number;
  version: number;
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

const CATEGORY_COLORS: Record<string, string> = {
  behavior: "#6cffa0",
  tools: "#6c8aff",
  safety: "#ff6c8a",
  context: "#ffcc44",
};

export default function PromptsTab() {
  const [subTab, setSubTab] = useState<SubTab>("fragments");
  const [fragments, setFragments] = useState<Fragment[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [editing, setEditing] = useState<Fragment | null>(null);
  const [editingTemplate, setEditingTemplate] = useState<Template | null>(null);
  const [versions, setVersions] = useState<Version[]>([]);
  const [showVersions, setShowVersions] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [genPurpose, setGenPurpose] = useState("");
  const [genCategory, setGenCategory] = useState("behavior");
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
        fetch(`${API}/fragments`),
        fetch(`${API}/templates`),
      ]);
      setFragments(await fRes.json());
      setTemplates(await tRes.json());
    } catch { /* API not available */ }
  }, []);

  useEffect(() => { fetchAll(); }, [fetchAll]);

  const showMsg = (m: string) => { setMsg(m); setTimeout(() => setMsg(""), 3000); };

  const saveFragment = async (frag: Fragment, changeReason: string) => {
    const res = await fetch(`${API}/fragments/${frag.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        display_name: frag.display_name,
        category: frag.category,
        content: frag.content,
        description: frag.description,
        is_active: !!frag.is_active,
        change_reason: changeReason,
      }),
    });
    if (res.ok) { showMsg("Fragment saved"); setEditing(null); await fetchAll(); }
    else showMsg("Failed to save");
  };

  const createFragment = async (frag: { name: string; display_name: string; category: string; content: string; description: string }) => {
    const res = await fetch(`${API}/fragments`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(frag),
    });
    if (res.ok) { showMsg("Fragment created"); setCreating(false); await fetchAll(); }
    else { const d = await res.json(); showMsg(`Error: ${d.detail}`); }
  };

  const deleteFragment = async (id: string) => {
    if (!confirm("Delete this fragment?")) return;
    const res = await fetch(`${API}/fragments/${id}`, { method: "DELETE" });
    if (res.ok) { showMsg("Deleted"); await fetchAll(); }
    else { const d = await res.json(); showMsg(`Error: ${d.detail}`); }
  };

  const loadVersions = async (type: "fragments" | "templates", id: string) => {
    const res = await fetch(`${API}/${type}/${id}/versions`);
    if (res.ok) { setVersions(await res.json()); setShowVersions(id); }
  };

  const rollback = async (type: "fragments" | "templates", id: string, version: number) => {
    const res = await fetch(`${API}/${type}/${id}/rollback/${version}`, { method: "POST" });
    if (res.ok) { showMsg(`Rolled back to v${version}`); await fetchAll(); loadVersions(type, id); }
  };

  const generateFragment = async () => {
    if (!genPurpose.trim()) return;
    setGenerating(true);
    try {
      const res = await fetch(`${API}/generate/fragment`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ purpose: genPurpose, category: genCategory }),
      });
      const data = await res.json();
      if (res.ok) {
        setEditing({
          id: "", name: "", display_name: genPurpose, category: genCategory,
          content: data.generated_fragment, description: "",
          is_active: 1, is_system: 0, agent_count: 0, version: 0,
        } as Fragment);
        setCreating(true);
        setGenPurpose("");
      } else showMsg("Generation failed");
    } catch { showMsg("Generation failed"); }
    setGenerating(false);
  };

  const generateSystemPrompt = async () => {
    setAiGenerating(true);
    try {
      const res = await fetch(`${API}/generate/system-prompt`, {
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
    const res = await fetch(`${API}/templates/${tmpl.id}`, {
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
          {/* AI generate bar */}
          <div style={s.genBar}>
            <input style={{ ...s.input, flex: 1 }} placeholder="Describe a fragment to generate with AI..." value={genPurpose} onChange={(e) => setGenPurpose(e.target.value)} />
            <select style={{ ...s.select, width: "140px" }} value={genCategory} onChange={(e) => setGenCategory(e.target.value)}>
              <option value="behavior">Behavior</option>
              <option value="tools">Tools</option>
              <option value="safety">Safety</option>
              <option value="context">Context</option>
            </select>
            <button style={{ ...s.btn, opacity: generating ? 0.5 : 1 }} onClick={generateFragment} disabled={generating || !genPurpose.trim()}>
              {generating ? "✨ Generating..." : "✨ Generate"}
            </button>
            <button style={s.btnSecondary} onClick={() => setCreating(true)}>+ New</button>
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

          {/* Creating new fragment */}
          {creating && <FragmentEditor
            key="__new_fragment__"
            fragment={editing || { id: "", name: "", display_name: "", category: "behavior", content: "", description: "", is_active: 1, is_system: 0, agent_count: 0, version: 0 } as Fragment}
            isNew
            onSave={(f) => createFragment({ name: f.name, display_name: f.display_name, category: f.category, content: f.content, description: f.description })}
            onCancel={() => { setCreating(false); setEditing(null); }}
          />}

          {/* Fragment list */}
          {fragments.map((f) => (
            <div key={f.id} style={s.card}>
              {editing?.id === f.id ? (
                <FragmentEditor fragment={editing} onSave={(upd) => saveFragment(upd, "Updated from UI")} onCancel={() => setEditing(null)} />
              ) : (
                <>
                  <div style={s.cardHeader}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                      <span style={{ ...s.badge, backgroundColor: CATEGORY_COLORS[f.category] || "#888" }}>{f.category}</span>
                      <strong style={{ color: "#e0e0e8" }}>{f.display_name}</strong>
                      <span style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>v{f.version}</span>
                      {f.is_system ? <span style={{ ...s.badge, backgroundColor: "#555" }}>system</span> : null}
                      {!f.is_active ? <span style={{ ...s.badge, backgroundColor: "#aa4444" }}>disabled</span> : null}
                    </div>
                    <div style={{ display: "flex", gap: "6px" }}>
                      <button style={s.btnSmall} onClick={() => setEditing({ ...f })}>Edit</button>
                      <button style={s.btnSmall} onClick={() => loadVersions("fragments", f.id)}>History</button>
                      {!f.is_system && <button style={{ ...s.btnSmall, color: "#ff6c8a" }} onClick={() => deleteFragment(f.id)}>Delete</button>}
                    </div>
                  </div>
                  <p style={{ color: "#8888a0", fontSize: "0.85rem", margin: "4px 0" }}>{f.description}</p>
                  <div style={s.cardMeta}>
                    Used by {f.agent_count} agent{f.agent_count !== 1 ? "s" : ""}
                  </div>
                  <pre style={s.pre}>{f.content.substring(0, 300)}{f.content.length > 300 ? "..." : ""}</pre>
                </>
              )}

              {/* Version history */}
              {showVersions === f.id && (
                <div style={s.versionPanel}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" }}>
                    <strong style={{ color: "#e0e0e8" }}>Version History</strong>
                    <button style={s.btnSmall} onClick={() => setShowVersions(null)}>Close</button>
                  </div>
                  {versions.map((v) => (
                    <div key={v.id} style={s.versionRow}>
                      <span style={{ color: "#6c8aff" }}>v{v.version}</span>
                      <span style={{ color: "#8888a0", flex: 1 }}>{v.change_reason}</span>
                      <span style={{ color: "#5a5a6e", fontSize: "0.8rem" }}>{v.changed_by} · {new Date(v.created_at).toLocaleDateString()}</span>
                      <button style={s.btnSmall} onClick={() => rollback("fragments", f.id, v.version)}>Restore</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* ═══ TEMPLATES TAB ═══ */}
      {subTab === "templates" && (
        <div>
          {templates.map((t) => (
            <div key={t.id} style={s.card}>
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
                  {versions.map((v) => (
                    <div key={v.id} style={s.versionRow}>
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

// ── Fragment Editor ──

function FragmentEditor({ fragment, isNew, onSave, onCancel }: {
  fragment: Fragment;
  isNew?: boolean;
  onSave: (f: Fragment) => void;
  onCancel: () => void;
}) {
  const [f, setF] = useState({ ...fragment });
  const [name, setName] = useState(fragment.name);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
      {isNew && (
        <input style={s.input} placeholder="fragment-slug-name" value={name} onChange={(e) => setName(e.target.value)} />
      )}
      <input style={s.input} placeholder="Display Name" value={f.display_name} onChange={(e) => setF({ ...f, display_name: e.target.value })} />
      <select style={s.select} value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
        <option value="behavior">Behavior</option>
        <option value="tools">Tools</option>
        <option value="safety">Safety</option>
        <option value="context">Context</option>
      </select>
      <input style={s.input} placeholder="Description" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      <textarea style={{ ...s.textarea, height: "250px" }} value={f.content} onChange={(e) => setF({ ...f, content: e.target.value })} />
      <div style={{ display: "flex", gap: "8px" }}>
        <button style={s.btn} onClick={() => onSave(isNew ? { ...f, name } as Fragment : f)}>
          {isNew ? "Create" : "Save"}
        </button>
        <button style={s.btnSecondary} onClick={onCancel}>Cancel</button>
      </div>
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
    background: "none", border: "1px solid #2a2a3e", borderRadius: "8px",
    color: "#8888a0", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer",
  },
  subTabActive: { color: "#6c8aff", borderColor: "#6c8aff", backgroundColor: "#1a1a2e" },
  genBar: { display: "flex", gap: "8px", alignItems: "center" },
  section: { backgroundColor: "#12121a", borderRadius: "12px", padding: "16px", border: "1px solid #1e1e2e" },
  card: { backgroundColor: "#12121a", borderRadius: "12px", padding: "16px", border: "1px solid #1e1e2e" },
  cardHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  cardMeta: { color: "#5a5a6e", fontSize: "0.8rem", marginBottom: "8px" },
  badge: { fontSize: "0.7rem", padding: "2px 8px", borderRadius: "4px", color: "#000", fontWeight: 600, textTransform: "uppercase" as const },
  pre: { backgroundColor: "#0a0a14", borderRadius: "8px", padding: "12px", color: "#8888a0", fontSize: "0.8rem", margin: 0, whiteSpace: "pre-wrap" as const, overflow: "hidden", maxHeight: "100px" },
  versionPanel: { marginTop: "12px", padding: "12px", backgroundColor: "#0a0a14", borderRadius: "8px" },
  versionRow: { display: "flex", gap: "12px", alignItems: "center", padding: "6px 0", borderBottom: "1px solid #1e1e2e" },
  input: { backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none" },
  select: { backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.9rem", outline: "none" },
  textarea: { backgroundColor: "#1e1e2e", border: "1px solid #2a2a3e", borderRadius: "8px", padding: "12px", color: "#e0e0e8", fontSize: "0.85rem", outline: "none", fontFamily: "monospace", resize: "vertical" as const, width: "100%", boxSizing: "border-box" as const },
  btn: { backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: "8px", padding: "10px 20px", fontSize: "0.85rem", fontWeight: 600, cursor: "pointer" },
  btnSecondary: { backgroundColor: "#2a2a3e", color: "#e0e0e8", border: "none", borderRadius: "8px", padding: "10px 20px", fontSize: "0.85rem", cursor: "pointer" },
  btnSmall: { background: "none", border: "1px solid #2a2a3e", borderRadius: "6px", padding: "4px 10px", color: "#8888a0", fontSize: "0.8rem", cursor: "pointer" },
  msg: { fontSize: "0.85rem", padding: "8px 12px", borderRadius: "8px", backgroundColor: "#1a1a2e" },
};
