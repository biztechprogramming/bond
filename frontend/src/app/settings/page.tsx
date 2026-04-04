"use client";

import React, { useEffect, useState, useCallback } from "react";
import AgentsTab from "./agents/AgentsTab";
import DeploymentTab from "./deployment/DeploymentTab";
import PromptsTab from "./prompts/PromptsTab";
import ChannelsTab from "./channels/ChannelsTab";
import SkillsTab from "./skills/SkillsTab";
import OptimizationTab from "./optimization/OptimizationTab";
import ContainerHostsTab from "./containers/ContainerHostsTab";
import { BACKEND_API } from "@/lib/config";
import { useSettings, useProviderApiKeys } from "@/hooks/useSpacetimeDB";
import { getConnection } from "@/lib/spacetimedb-client";

const API_BASE = `${BACKEND_API}/settings`;

const TABS = [
  { id: "agents", label: "Agents" },
  { id: "containers", label: "Container Hosts" },
  { id: "deployment", label: "Deployment" },
  { id: "channels", label: "Channels" },
  { id: "prompts", label: "Prompts" },
  { id: "llm", label: "LLM" },
  { id: "embedding", label: "Embedding" },
  { id: "api-keys", label: "API Keys" },
  { id: "skills", label: "Skills" },
  { id: "optimization", label: "Optimization" },
] as const;

type TabId = (typeof TABS)[number]["id"];

// ── Embedding interfaces ──

interface EmbeddingModel {
  model_name: string;
  family: string;
  provider: string;
  max_dimension: number;
  supported_dimensions: number[];
  supports_local: boolean;
  supports_api: boolean;
  is_default: boolean;
}

interface EmbeddingCurrent {
  model: string;
  dimension: number;
  execution_mode: string;
  has_voyage_key: boolean;
  has_gemini_key: boolean;
}

interface LlmCurrent {
  provider: string;
  model: string;
  keys_set: Record<string, boolean>;
}

// ── Main Settings Page ──

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabId>("agents");

  // Read hash after hydration to avoid SSR mismatch
  useEffect(() => {
    const hash = window.location.hash.replace("#", "");
    if (TABS.some((t) => t.id === hash)) setActiveTab(hash as TabId);
  }, []);

  // Embedding state
  const [models, setModels] = useState<EmbeddingModel[]>([]);
  const [current, setCurrent] = useState<EmbeddingCurrent | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [selectedDimension, setSelectedDimension] = useState(0);
  const [selectedMode, setSelectedMode] = useState("auto");
  const [warning, setWarning] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  // SpacetimeDB-backed settings
  const settingsRows = useSettings();
  const allSettings = Object.fromEntries(settingsRows.map(s => [s.key, s.value]));
  const providerApiKeys = useProviderApiKeys();

  // LLM state
  const [llmCurrent, setLlmCurrent] = useState<LlmCurrent | null>(null);

  // API key state
  const [voyageKey, setVoyageKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [googleKey, setGoogleKey] = useState("");
  const [keySaveMsg, setKeySaveMsg] = useState("");

  const switchTab = (tab: TabId) => {
    setActiveTab(tab);
    window.history.replaceState(null, "", `#${tab}`);
  };

  const fetchSettings = useCallback(async () => {
    try {
      const [modelsRes, currentRes, llmCurrentRes] = await Promise.all([
        fetch(`${API_BASE}/embedding/models`),
        fetch(`${API_BASE}/embedding/current`),
        fetch(`${API_BASE}/llm/current`),
      ]);
      setModels(await modelsRes.json());
      const cur = await currentRes.json();
      setCurrent(cur);
      setSelectedModel(cur.model);
      setSelectedDimension(cur.dimension);
      setSelectedMode(cur.execution_mode);
      setLlmCurrent(await llmCurrentRes.json());
    } catch { /* API not available */ }
  }, []);

  useEffect(() => { fetchSettings(); }, [fetchSettings]);

  const activeModel = models.find((m) => m.model_name === selectedModel);

  useEffect(() => {
    if (!current || !activeModel) { setWarning(""); return; }
    const cur = models.find((m) => m.model_name === current.model);
    setWarning(cur && cur.family !== activeModel.family
      ? `Switching from ${cur.family} to ${activeModel.family} requires re-embedding.`
      : "");
  }, [selectedModel, current, activeModel, models]);

  useEffect(() => {
    if (activeModel && !activeModel.supported_dimensions.includes(selectedDimension))
      setSelectedDimension(activeModel.supported_dimensions[activeModel.supported_dimensions.length - 1]);
  }, [selectedModel, activeModel, selectedDimension]);

  const availableModes = () => {
    if (!activeModel) return [];
    const m: string[] = [];
    if (activeModel.supports_local) m.push("local");
    if (activeModel.supports_api) m.push("api");
    if (activeModel.supports_local && activeModel.supports_api) m.push("auto");
    return m;
  };

  useEffect(() => {
    const m = availableModes();
    if (m.length > 0 && !m.includes(selectedMode)) setSelectedMode(m[0]);
  }, [selectedModel, activeModel]);

  const saveEmbedding = async () => {
    setSaving(true); setSaveMsg("");
    try {
      const res = await fetch(`${API_BASE}/embedding`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedModel, dimension: selectedDimension, execution_mode: selectedMode }),
      });
      const data = await res.json();
      setSaveMsg(!res.ok ? `Error: ${data.detail}` : data.warning ? `Saved. Warning: ${data.warning}` : "Saved.");
      if (res.ok) await fetchSettings();
    } catch { setSaveMsg("Failed to save."); }
    setSaving(false);
  };

  const saveKey = async (fullKey: string, value: string, clearFn: (v: string) => void) => {
    if (!value.trim()) return;
    setKeySaveMsg("");
    try {
      const conn = getConnection();
      if (!conn) { setKeySaveMsg("Not connected."); return; }
      // fullKey is like "llm.api_key.anthropic" — extract provider from last segment
      const parts = fullKey.split(".");
      const providerId = parts[parts.length - 1];
      const keyType = parts.slice(0, parts.length - 1).join(".");
      conn.reducers.setProviderApiKey({ providerId, encryptedValue: value.trim(), keyType, createdAt: BigInt(0), updatedAt: BigInt(0) });
      setKeySaveMsg("Saved.");
      clearFn("");
    } catch { setKeySaveMsg("Failed."); }
  };

  const deleteKey = async (fullKey: string) => {
    setKeySaveMsg("");
    try {
      const conn = getConnection();
      if (!conn) { setKeySaveMsg("Not connected."); return; }
      const parts = fullKey.split(".");
      const providerId = parts[parts.length - 1];
      conn.reducers.deleteProviderApiKey({ providerId });
      setKeySaveMsg("Deleted.");
    } catch { setKeySaveMsg("Failed to delete."); }
  };

  const masked = (key: string) => {
    // key is like "llm.api_key.anthropic" — match by providerId (last segment) and keyType (rest)
    const parts = key.split(".");
    const providerId = parts[parts.length - 1];
    const keyType = parts.slice(0, parts.length - 1).join(".");
    const found = providerApiKeys.find(k => k.providerId === providerId && k.keyType === keyType);
    if (!found || !found.encryptedValue) return "";
    const v = found.encryptedValue;
    if (v.length <= 8) return "••••••••";
    return v.slice(0, 7) + "••••••••" + v.slice(-4);
  };

  // ── Render ──

  return (
    <div style={s.container}>
      <style>{`
        .settings-tab-bar::-webkit-scrollbar { display: none; }
        @media (max-width: 768px) {
          .settings-content-area { padding: 12px !important; gap: 16px !important; }
          .settings-header { padding: 12px 16px !important; }
          .settings-section { padding: 16px !important; }
        }
      `}</style>
      <header className="settings-header" style={s.header}>
        <a href="/" style={s.backLink}>&larr; Chat</a>
        <h1 style={s.title}>Settings</h1>
      </header>

      {/* Tab bar */}
      <div style={s.tabBarWrapper}>
        <div className="settings-tab-bar" style={s.tabBar}>
          {TABS.map((tab) => (
            <button
              key={tab.id}
              style={activeTab === tab.id ? { ...s.tab, ...s.tabActive } : s.tab}
              onClick={() => switchTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div style={s.tabBarFade} aria-hidden />
      </div>

      {/* Tab content */}
      <div className="settings-content-area" style={s.content}>
        {activeTab === "agents" && <AgentsTab />}

        {activeTab === "containers" && <ContainerHostsTab />}

        {activeTab === "deployment" && <DeploymentTab />}

        {activeTab === "channels" && <ChannelsTab />}

        {activeTab === "prompts" && <PromptsTab />}

        {activeTab === "llm" && (
          <section style={s.section}>
            <h2 style={s.sectionTitle}>LLM Configuration</h2>
            <div style={s.field}>
              <label style={s.label}>Provider</label>
              <div style={s.readOnly}>{llmCurrent?.provider || "anthropic"} (from bond.json)</div>
            </div>
            <div style={s.field}>
              <label style={s.label}>Model</label>
              <div style={s.readOnly}>{llmCurrent?.model || "claude-sonnet-4-20250514"} (from bond.json)</div>
            </div>
            {llmCurrent && (
              <div style={{ ...s.modelDetails, marginTop: "12px" }}>
                {Object.entries(llmCurrent.keys_set).map(([p, set]) => (
                  <span key={p}>{p}: {set ? "✅" : "❌"}</span>
                ))}
              </div>
            )}
            <div style={{ ...s.field, marginTop: "20px" }}>
              <label style={s.label}>Turn Timeout (minutes)</label>
              <p style={{ color: "#5a5a6e", fontSize: "0.8rem", margin: "0 0 8px 0" }}>
                Maximum time an agent can work on a single turn before the request times out. Increase for complex tasks with many tool calls.
              </p>
              <div style={s.keyRow}>
                <input
                  type="number"
                  style={{ ...s.input, width: "100px" }}
                  defaultValue={allSettings["agent.turn_timeout_minutes"] || "30"}
                  min={1}
                  max={120}
                  onBlur={async (e) => {
                    const val = e.target.value.trim();
                    if (!val || parseInt(val) < 1) return;
                    try {
                      const conn = getConnection();
                      if (!conn) { setSaveMsg("Not connected."); return; }
                      conn.reducers.setSetting({ key: "agent.turn_timeout_minutes", value: val, keyType: "string" });
                      setSaveMsg("Turn timeout saved.");
                    } catch { setSaveMsg("Failed to save."); }
                  }}
                />
                <span style={{ color: "#8888a0", fontSize: "0.9rem", alignSelf: "center" }}>minutes</span>
              </div>
            </div>

            {/* Coding Agent Output Settings */}
            <div style={{ borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#1e1e2e", marginTop: "24px", paddingTop: "20px" }}>
              <h3 style={{ color: "#e0e0e8", fontSize: "0.95rem", marginBottom: "12px" }}>Coding Agent Output</h3>
              <p style={{ color: "#5a5a6e", fontSize: "0.8rem", margin: "0 0 16px 0" }}>
                Control how coding agent (Claude Code, Codex, etc.) output is captured while running in the background.
              </p>

              <div style={s.field}>
                <label style={{ ...s.label, display: "flex", alignItems: "center", gap: "10px", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={allSettings["coding_agent.log_to_file"] !== "false"}
                    onChange={async (e) => {
                      const val = e.target.checked ? "true" : "false";
                      try {
                        const conn = getConnection();
                        if (!conn) { setSaveMsg("Not connected."); return; }
                        conn.reducers.setSetting({ key: "coding_agent.log_to_file", value: val, keyType: "string" });
                        setSaveMsg("Saved.");
                      } catch { setSaveMsg("Failed to save."); }
                    }}
                    style={{ accentColor: "#6c8aff", width: "16px", height: "16px" }}
                  />
                  <span style={{ color: "#e0e0e8", fontSize: "0.9rem" }}>Log output to file</span>
                </label>
                <p style={{ color: "#5a5a6e", fontSize: "0.78rem", margin: "4px 0 0 26px" }}>
                  Write agent stdout to a log file on disk. Useful for debugging and post-mortem analysis.
                </p>
              </div>

              <div style={s.field}>
                <label style={{ ...s.label, display: "flex", alignItems: "center", gap: "10px", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={allSettings["coding_agent.stream_output"] !== "false"}
                    onChange={async (e) => {
                      const val = e.target.checked ? "true" : "false";
                      try {
                        const conn = getConnection();
                        if (!conn) { setSaveMsg("Not connected."); return; }
                        conn.reducers.setSetting({ key: "coding_agent.stream_output", value: val, keyType: "string" });
                        setSaveMsg("Saved.");
                      } catch { setSaveMsg("Failed to save."); }
                    }}
                    style={{ accentColor: "#6c8aff", width: "16px", height: "16px" }}
                  />
                  <span style={{ color: "#e0e0e8", fontSize: "0.9rem" }}>Stream output to UI</span>
                </label>
                <p style={{ color: "#5a5a6e", fontSize: "0.78rem", margin: "4px 0 0 26px" }}>
                  Show live agent output in the chat panel. Disabling reduces network traffic for long-running agents.
                </p>
              </div>
            </div>
          </section>
        )}

        {activeTab === "embedding" && (
          <section style={s.section}>
            <h2 style={s.sectionTitle}>Embedding Model</h2>
            {warning && <div style={s.warning}>{warning}</div>}
            <div style={s.field}>
              <label style={s.label}>Model</label>
              <p style={s.helpText}>
                The embedding model converts text into numerical vectors for semantic search. Models in the same family share an embedding space and are interchangeable. Larger models are more accurate but slower and use more memory.
              </p>
              <select style={s.select} value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
                {models.map((m) => <option key={m.model_name} value={m.model_name}>{m.model_name} ({m.family})</option>)}
              </select>
            </div>
            {activeModel && (
              <div style={s.modelDetails}>
                <span>Family: {activeModel.family}</span>
                <span>Provider: {activeModel.provider}</span>
                <span>Local: {activeModel.supports_local ? "Yes" : "No"}</span>
                <span>API: {activeModel.supports_api ? "Yes" : "No"}</span>
                <span>Max dim: {activeModel.max_dimension}</span>
              </div>
            )}
            <div style={s.field}>
              <label style={s.label}>Dimension</label>
              <p style={s.helpText}>
                The number of dimensions in each embedding vector. Higher dimensions capture more nuance but use more storage and compute. 1024 is a good default for most use cases.
              </p>
              <select style={s.select} value={selectedDimension} onChange={(e) => setSelectedDimension(Number(e.target.value))}>
                {activeModel?.supported_dimensions.map((d) => <option key={d} value={d}>{d}</option>)}
              </select>
            </div>
            <div style={s.field}>
              <label style={s.label}>Execution Mode</label>
              <p style={s.helpText}>
                How embeddings are generated. &quot;local&quot; runs the model on this machine (free, private, but slower). &quot;api&quot; calls the Voyage AI API (fast, requires API key). &quot;auto&quot; tries API providers first, falls back to local.
              </p>
              <div style={s.radioGroup}>
                {availableModes().map((mode) => (
                  <label key={mode} style={s.radioLabel}>
                    <input type="radio" name="exec_mode" value={mode} checked={selectedMode === mode} onChange={() => setSelectedMode(mode)} style={s.radio} />
                    {mode}
                  </label>
                ))}
              </div>
            </div>
            {current && (
              <div style={{ ...s.modelDetails, marginBottom: "16px" }}>
                <span>Voyage API key: {current.has_voyage_key ? "configured" : "not set"}</span>
                <span>Gemini API key: {current.has_gemini_key ? "configured" : "not set"}</span>
              </div>
            )}
            <button style={{ ...s.button, opacity: saving ? 0.5 : 1 }} onClick={saveEmbedding} disabled={saving}>
              {saving ? "Saving..." : "Save Embedding Settings"}
            </button>
            {saveMsg && <div style={{ ...s.msg, color: saveMsg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{saveMsg}</div>}
          </section>
        )}

        {activeTab === "api-keys" && (
          <section style={s.section}>
            <h2 style={s.sectionTitle}>API Keys</h2>
            <p style={{ color: "#8888a0", fontSize: "0.85rem", marginBottom: "20px" }}>All keys are encrypted at rest.</p>

            <h3 style={{ color: "#e0e0e8", fontSize: "0.95rem", marginBottom: "12px" }}>LLM Providers</h3>
            {[
              { label: "Anthropic", key: "llm.api_key.anthropic", state: anthropicKey, set: setAnthropicKey, placeholder: "sk-ant-..." },
              { label: "OpenAI", key: "llm.api_key.openai", state: openaiKey, set: setOpenaiKey, placeholder: "sk-..." },
              { label: "Google", key: "llm.api_key.google", state: googleKey, set: setGoogleKey, placeholder: "Google API key" },
            ].map(({ label, key, state, set, placeholder }) => (
              <div key={key} style={s.field}>
                <label style={s.label}>
                  {label} {masked(key) && <span style={s.masked}>Current: {masked(key)}</span>}
                </label>
                <div style={s.keyRow}>
                  <input type="password" style={s.input} value={state} onChange={(e) => set(e.target.value)} placeholder={placeholder} />
                  <button style={s.button} onClick={() => saveKey(key, state, set)}>Save</button>
                  {masked(key) && <button style={s.deleteBtn} onClick={() => deleteKey(key)}>Delete</button>}
                </div>
              </div>
            ))}

            <h3 style={{ color: "#e0e0e8", fontSize: "0.95rem", margin: "24px 0 12px" }}>Embedding Providers</h3>
            {[
              { label: "Voyage AI", key: "embedding.api_key.voyage", state: voyageKey, set: setVoyageKey, placeholder: "Voyage API key" },
              { label: "Gemini", key: "embedding.api_key.gemini", state: geminiKey, set: setGeminiKey, placeholder: "Gemini API key" },
            ].map(({ label, key, state, set, placeholder }) => (
              <div key={key} style={s.field}>
                <label style={s.label}>
                  {label} {masked(key) && <span style={s.masked}>Current: {masked(key)}</span>}
                </label>
                <div style={s.keyRow}>
                  <input type="password" style={s.input} value={state} onChange={(e) => set(e.target.value)} placeholder={placeholder} />
                  <button style={s.button} onClick={() => saveKey(key, state, set)}>Save</button>
                  {masked(key) && <button style={s.deleteBtn} onClick={() => deleteKey(key)}>Delete</button>}
                </div>
              </div>
            ))}

            {keySaveMsg && <div style={{ ...s.msg, color: "#6cffa0" }}>{keySaveMsg}</div>}
          </section>
        )}

        {activeTab === "skills" && <SkillsTab />}

        {activeTab === "optimization" && <OptimizationTab />}
      </div>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", height: "100vh", maxWidth: "1200px", margin: "0 auto", width: "100%", overflow: "hidden" },
  header: { display: "flex", alignItems: "center", gap: "16px", padding: "16px 24px", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e", flexShrink: 0 },
  backLink: { color: "#6c8aff", textDecoration: "none", fontSize: "0.9rem" },
  title: { fontSize: "1.5rem", fontWeight: 700, margin: 0 },
  tabBarWrapper: { position: "relative" as const, borderBottomWidth: "1px", borderBottomStyle: "solid" as const, borderBottomColor: "#1e1e2e", flexShrink: 0 },
  tabBar: { display: "flex", padding: "0 24px", overflowX: "auto" as const, scrollbarWidth: "none" as const, msOverflowStyle: "none" as const, WebkitOverflowScrolling: "touch" as const, flexWrap: "nowrap" as const },
  tabBarFade: { position: "absolute" as const, top: 0, right: 0, bottom: 0, width: "40px", background: "linear-gradient(to right, transparent, #0a0a12)", pointerEvents: "none" as const },
  tab: {
    background: "none", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderBottomWidth: "2px", borderBottomStyle: "solid" as const, borderBottomColor: "transparent",
    color: "#8888a0", padding: "12px 20px", fontSize: "0.9rem", fontWeight: 500,
    cursor: "pointer", transition: "color 0.2s, border-color 0.2s", whiteSpace: "nowrap" as const, flexShrink: 0,
  },
  tabActive: { color: "#6c8aff", borderBottomColor: "#6c8aff" },
  content: { flex: 1, overflowY: "auto", padding: "24px", display: "flex", flexDirection: "column", gap: "24px", minHeight: 0, WebkitOverflowScrolling: "touch" as any },
  section: { backgroundColor: "#12121a", borderRadius: "12px", padding: "24px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },
  sectionTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 20px 0" },
  field: { marginBottom: "16px" },
  label: { display: "block", fontSize: "0.85rem", color: "#8888a0", marginBottom: "6px", fontWeight: 500 },
  select: { width: "100%", backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.95rem", outline: "none" },
  input: { flex: 1, backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.95rem", outline: "none" },
  modelDetails: { display: "flex", gap: "16px", flexWrap: "wrap" as const, fontSize: "0.8rem", color: "#8888a0", marginBottom: "16px", padding: "8px 12px", backgroundColor: "#1e1e2e", borderRadius: "8px" },
  radioGroup: { display: "flex", gap: "20px" },
  radioLabel: { display: "flex", alignItems: "center", gap: "6px", color: "#e0e0e8", fontSize: "0.95rem", cursor: "pointer" },
  radio: { accentColor: "#6c8aff" },
  button: { backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "10px 20px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer" },
  keyRow: { display: "flex", gap: "8px" },
  deleteBtn: { backgroundColor: "#3a1a1e", color: "#ff6c8a", borderWidth: "1px", borderStyle: "solid", borderColor: "#5a2a2e", borderRadius: "8px", padding: "10px 16px", cursor: "pointer", fontWeight: 500, fontSize: "0.9rem", whiteSpace: "nowrap" },
  masked: { color: "#6c8aff", fontSize: "0.8rem", marginLeft: "8px" },
  warning: { backgroundColor: "#2a2a1a", borderWidth: "1px", borderStyle: "solid", borderColor: "#aa8800", borderRadius: "8px", padding: "12px 16px", color: "#ffcc44", fontSize: "0.85rem", marginBottom: "16px" },
  helpText: { color: "#5a5a6e", fontSize: "0.8rem", margin: "0 0 8px 0", lineHeight: "1.4" },
  msg: { marginTop: "12px", fontSize: "0.85rem" },
  readOnly: { color: "#8888a0", fontSize: "0.95rem", padding: "10px 12px", backgroundColor: "#1e1e2e", borderRadius: "8px" },
};
