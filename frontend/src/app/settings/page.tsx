"use client";

import React, { useEffect, useState, useCallback } from "react";
import AgentsTab from "./agents/AgentsTab";
import PromptsTab from "./prompts/PromptsTab";
import MCPTab from "./mcp/MCPTab";

const API_BASE = "http://localhost:18790/api/v1/settings";

const TABS = [
  { id: "agents", label: "Agents" },
  { id: "mcp", label: "MCP" },
  { id: "prompts", label: "Prompts" },
  { id: "llm", label: "LLM" },
  { id: "embedding", label: "Embedding" },
  { id: "api-keys", label: "API Keys" },
] as const;

type TabId = (typeof TABS)[number]["id"];

// ── Embedding interfaces ──────────────────────────────────────────────────

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

// ── Main Settings Page ──────────────────────────────────────────────────────

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
  const [allSettings, setAllSettings] = useState<Record<string, string>>({});

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
      const [modelsRes, currentRes, settingsRes, llmCurrentRes] = await Promise.all([
        fetch(`${API_BASE}/embedding/models`),
        fetch(`${API_BASE}/embedding/current`),
        fetch(API_BASE),
        fetch(`${API_BASE}/llm/current`),
      ]);
      setModels(await modelsRes.json());
      const cur = await currentRes.json();
      setCurrent(cur);
      setSelectedModel(cur.model);
      setSelectedDimension(cur.dimension);
      setSelectedMode(cur.execution_mode);
      const settings = await settingsRes.json();
      setAllSettings(settings);
      setLlmCurrent(await llmCurrentRes.json());
    } catch (err) {
      console.error("Failed to fetch settings", err);
    }
  }, []);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  const saveEmbedding = async () => {
    setSaving(true);
    setSaveMsg("");
    try {
      const res = await fetch(`${API_BASE}/embedding/current`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: selectedModel,
          dimension: selectedDimension,
          execution_mode: selectedMode,
        }),
      });
      if (!res.ok) throw new Error("Failed to save");
      setSaveMsg("Settings saved!");
      fetchSettings();
    } catch (err) {
      setSaveMsg("Error saving settings.");
    } finally {
      setSaving(false);
      setTimeout(() => setSaveMsg(""), 3000);
    }
  };

  const saveKeys = async () => {
    setSaving(true);
    setKeySaveMsg("");
    try {
      const keys = {
        VOYAGE_API_KEY: voyageKey,
        GEMINI_API_KEY: geminiKey,
        ANTHROPIC_API_KEY: anthropicKey,
        OPENAI_API_KEY: openaiKey,
        GOOGLE_API_KEY: googleKey,
      };
      // Filter out empty keys
      const toSave = Object.fromEntries(
        Object.entries(keys).filter(([_, v]) => v.trim() !== "")
      );
      if (Object.keys(toSave).length === 0) return;

      const res = await fetch(API_BASE, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(toSave),
      });
      if (!res.ok) throw new Error("Failed to save keys");
      setKeySaveMsg("Keys saved!");
      setVoyageKey("");
      setGeminiKey("");
      setAnthropicKey("");
      setOpenaiKey("");
      setGoogleKey("");
      fetchSettings();
    } catch (err) {
      setKeySaveMsg("Error saving keys.");
    } finally {
      setSaving(false);
      setTimeout(() => setKeySaveMsg(""), 3000);
    }
  };

  return (
    <div className="flex flex-col h-full bg-gray-50 overflow-hidden">
      <header className="bg-white border-b border-gray-200 px-6 py-4">
        <h1 className="text-2xl font-bold text-gray-900">Settings</h1>
      </header>

      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <aside className="w-64 bg-white border-r border-gray-200 overflow-y-auto">
          <nav className="p-4 space-y-1">
            {TABS.map((tab) => (
              <button
                key={tab.id}
                onClick={() => switchTab(tab.id)}
                className={`w-full text-left px-3 py-2 text-sm font-medium rounded-md transition-colors ${
                  activeTab === tab.id
                    ? "bg-blue-50 text-blue-700"
                    : "text-gray-600 hover:bg-gray-50 hover:text-gray-900"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </nav>
        </aside>

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-8">
          <div className="max-w-4xl mx-auto">
            {activeTab === "agents" && <AgentsTab />}
            {activeTab === "mcp" && <MCPTab />}
            {activeTab === "prompts" && <PromptsTab />}

            {activeTab === "llm" && (
              <div className="space-y-6">
                <div>
                  <h2 className="text-xl font-semibold">LLM Configuration</h2>
                  <p className="text-sm text-gray-500">View current model and provider.</p>
                </div>
                {llmCurrent && (
                  <div className="bg-white shadow sm:rounded-lg p-6 border border-gray-200">
                    <dl className="grid grid-cols-1 gap-x-4 gap-y-6 sm:grid-cols-2">
                      <div>
                        <dt className="text-sm font-medium text-gray-500">Provider</dt>
                        <dd className="mt-1 text-sm text-gray-900 capitalize">{llmCurrent.provider}</dd>
                      </div>
                      <div>
                        <dt className="text-sm font-medium text-gray-500">Model</dt>
                        <dd className="mt-1 text-sm text-gray-900">{llmCurrent.model}</dd>
                      </div>
                    </dl>
                  </div>
                )}
              </div>
            )}

            {activeTab === "embedding" && (
              <div className="space-y-6">
                <div>
                  <h2 className="text-xl font-semibold">Embedding Model</h2>
                  <p className="text-sm text-gray-500">Configure the model used for search and RAG.</p>
                </div>

                <div className="bg-white shadow sm:rounded-lg p-6 border border-gray-200 space-y-6">
                  <div>
                    <label className="block text-sm font-medium text-gray-700">Model</label>
                    <select
                      value={selectedModel}
                      onChange={(e) => {
                        const m = models.find((m) => m.model_name === e.target.value);
                        if (m) {
                          setSelectedModel(m.model_name);
                          setSelectedDimension(m.max_dimension);
                        }
                      }}
                      className="mt-1 block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm rounded-md"
                    >
                      {models.map((m) => (
                        <option key={m.model_name} value={m.model_name}>
                          {m.model_name} ({m.provider})
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-700">Dimension</label>
                    <select
                      value={selectedDimension}
                      onChange={(e) => setSelectedDimension(Number(e.target.value))}
                      className="mt-1 block w-full pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-blue-500 focus:border-blue-500 sm:text-sm rounded-md"
                    >
                      {models
                        .find((m) => m.model_name === selectedModel)
                        ?.supported_dimensions.map((d) => (
                          <option key={d} value={d}>
                            {d}
                          </option>
                        ))}
                    </select>
                  </div>

                  <div className="pt-4 border-t border-gray-100 flex items-center justify-between">
                    {saveMsg && <span className="text-sm text-green-600">{saveMsg}</span>}
                    <button
                      onClick={saveEmbedding}
                      disabled={saving}
                      className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium disabled:opacity-50"
                    >
                      {saving ? "Saving..." : "Save Changes"}
                    </button>
                  </div>
                </div>
              </div>
            )}

            {activeTab === "api-keys" && (
              <div className="space-y-6">
                <div>
                  <h2 className="text-xl font-semibold">API Keys</h2>
                  <p className="text-sm text-gray-500">Securely manage your provider credentials.</p>
                </div>

                <div className="bg-white shadow sm:rounded-lg p-6 border border-gray-200 space-y-4">
                  {[
                    { label: "Anthropic API Key", value: anthropicKey, setter: setAnthropicKey, id: "ANTHROPIC_API_KEY" },
                    { label: "OpenAI API Key", value: openaiKey, setter: setOpenaiKey, id: "OPENAI_API_KEY" },
                    { label: "Voyage API Key", value: voyageKey, setter: setVoyageKey, id: "VOYAGE_API_KEY" },
                    { label: "Gemini API Key", value: geminiKey, setter: setGeminiKey, id: "GEMINI_API_KEY" },
                    { label: "Google Search API Key", value: googleKey, setter: setGoogleKey, id: "GOOGLE_API_KEY" },
                  ].map((field) => (
                    <div key={field.id}>
                      <label className="block text-sm font-medium text-gray-700">{field.label}</label>
                      <input
                        type="password"
                        placeholder={llmCurrent?.keys_set[field.id] ? "••••••••••••••••" : "Not set"}
                        value={field.value}
                        onChange={(e) => field.setter(e.target.value)}
                        className="mt-1 block w-full border-gray-300 rounded-md shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                      />
                    </div>
                  ))}

                  <div className="pt-4 border-t border-gray-100 flex items-center justify-between">
                    {keySaveMsg && <span className="text-sm text-green-600">{keySaveMsg}</span>}
                    <button
                      onClick={saveKeys}
                      disabled={saving}
                      className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-md text-sm font-medium disabled:opacity-50"
                    >
                      {saving ? "Saving..." : "Save Keys"}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
}
