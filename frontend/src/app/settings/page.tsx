"use client";

import { useEffect, useState, useCallback } from "react";

const API_BASE = "http://localhost:18790/api/v1/settings";

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

export default function SettingsPage() {
  const [models, setModels] = useState<EmbeddingModel[]>([]);
  const [current, setCurrent] = useState<EmbeddingCurrent | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [selectedDimension, setSelectedDimension] = useState(0);
  const [selectedMode, setSelectedMode] = useState("auto");
  const [warning, setWarning] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  const [voyageKey, setVoyageKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [voyageMasked, setVoyageMasked] = useState("");
  const [geminiMasked, setGeminiMasked] = useState("");
  const [keySaveMsg, setKeySaveMsg] = useState("");

  const [allSettings, setAllSettings] = useState<Record<string, string>>({});

  const fetchData = useCallback(async () => {
    try {
      const [modelsRes, currentRes, settingsRes] = await Promise.all([
        fetch(`${API_BASE}/embedding/models`),
        fetch(`${API_BASE}/embedding/current`),
        fetch(API_BASE),
      ]);
      const modelsData: EmbeddingModel[] = await modelsRes.json();
      const currentData: EmbeddingCurrent = await currentRes.json();
      const settingsData: Record<string, string> = await settingsRes.json();

      setModels(modelsData);
      setCurrent(currentData);
      setAllSettings(settingsData);
      setSelectedModel(currentData.model);
      setSelectedDimension(currentData.dimension);
      setSelectedMode(currentData.execution_mode);

      setVoyageMasked(settingsData["embedding.api_key.voyage"] || "");
      setGeminiMasked(settingsData["embedding.api_key.gemini"] || "");
    } catch {
      // API not available
    }
  }, []);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  const activeModel = models.find((m) => m.model_name === selectedModel);

  // Check for family switch warning
  useEffect(() => {
    if (!current || !activeModel) {
      setWarning("");
      return;
    }
    const currentModelInfo = models.find((m) => m.model_name === current.model);
    if (currentModelInfo && currentModelInfo.family !== activeModel.family) {
      setWarning(
        `Switching from ${currentModelInfo.family} to ${activeModel.family} family requires re-embedding all content.`
      );
    } else {
      setWarning("");
    }
  }, [selectedModel, current, activeModel, models]);

  // Reset dimension when model changes
  useEffect(() => {
    if (activeModel) {
      const dims = activeModel.supported_dimensions;
      if (!dims.includes(selectedDimension)) {
        setSelectedDimension(dims[dims.length - 1]);
      }
    }
  }, [selectedModel, activeModel, selectedDimension]);

  const availableModes = () => {
    if (!activeModel) return [];
    const modes: string[] = [];
    if (activeModel.supports_local) modes.push("local");
    if (activeModel.supports_api) modes.push("api");
    if (activeModel.supports_local && activeModel.supports_api) modes.push("auto");
    return modes;
  };

  // Reset mode if not available
  useEffect(() => {
    const modes = availableModes();
    if (modes.length > 0 && !modes.includes(selectedMode)) {
      setSelectedMode(modes[0]);
    }
  }, [selectedModel, activeModel]);

  const saveEmbedding = async () => {
    setSaving(true);
    setSaveMsg("");
    try {
      const res = await fetch(`${API_BASE}/embedding`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: selectedModel,
          dimension: selectedDimension,
          execution_mode: selectedMode,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setSaveMsg(`Error: ${data.detail}`);
      } else {
        setSaveMsg(data.warning ? `Saved. Warning: ${data.warning}` : "Saved successfully.");
        await fetchData();
      }
    } catch {
      setSaveMsg("Failed to save.");
    }
    setSaving(false);
  };

  const saveApiKey = async (keyName: string, value: string) => {
    if (!value.trim()) return;
    setKeySaveMsg("");
    try {
      const res = await fetch(`${API_BASE}/embedding.api_key.${keyName}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: value.trim() }),
      });
      if (res.ok) {
        setKeySaveMsg(`${keyName} key saved.`);
        if (keyName === "voyage") setVoyageKey("");
        else setGeminiKey("");
        await fetchData();
      } else {
        setKeySaveMsg("Failed to save key.");
      }
    } catch {
      setKeySaveMsg("Failed to save key.");
    }
  };

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
          <a href="/" style={styles.backLink}>
            &larr; Chat
          </a>
          <h1 style={styles.title}>Settings</h1>
        </div>
      </header>

      <div style={styles.content}>
        {/* Embedding Model Section */}
        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Embedding Model</h2>

          {warning && <div style={styles.warning}>{warning}</div>}

          <div style={styles.field}>
            <label style={styles.label}>Model</label>
            <select
              style={styles.select}
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
            >
              {models.map((m) => (
                <option key={m.model_name} value={m.model_name}>
                  {m.model_name} ({m.family})
                </option>
              ))}
            </select>
          </div>

          {activeModel && (
            <div style={styles.modelDetails}>
              <span>Family: {activeModel.family}</span>
              <span>Provider: {activeModel.provider}</span>
              <span>Local: {activeModel.supports_local ? "Yes" : "No"}</span>
              <span>API: {activeModel.supports_api ? "Yes" : "No"}</span>
              <span>Max dim: {activeModel.max_dimension}</span>
            </div>
          )}

          <div style={styles.field}>
            <label style={styles.label}>Dimension</label>
            <select
              style={styles.select}
              value={selectedDimension}
              onChange={(e) => setSelectedDimension(Number(e.target.value))}
            >
              {activeModel?.supported_dimensions.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
          </div>

          <div style={styles.field}>
            <label style={styles.label}>Execution Mode</label>
            <div style={styles.radioGroup}>
              {availableModes().map((mode) => (
                <label key={mode} style={styles.radioLabel}>
                  <input
                    type="radio"
                    name="execution_mode"
                    value={mode}
                    checked={selectedMode === mode}
                    onChange={() => setSelectedMode(mode)}
                    style={styles.radio}
                  />
                  {mode}
                </label>
              ))}
            </div>
          </div>

          <button
            style={{ ...styles.button, opacity: saving ? 0.5 : 1 }}
            onClick={saveEmbedding}
            disabled={saving}
          >
            {saving ? "Saving..." : "Save Embedding Settings"}
          </button>
          {saveMsg && (
            <div style={{ ...styles.msg, color: saveMsg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>
              {saveMsg}
            </div>
          )}
        </section>

        {/* API Keys Section */}
        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>API Keys</h2>

          <div style={styles.field}>
            <label style={styles.label}>
              Voyage AI API Key {voyageMasked && <span style={styles.masked}>Current: {voyageMasked}</span>}
            </label>
            <div style={styles.keyRow}>
              <input
                type="password"
                style={styles.input}
                value={voyageKey}
                onChange={(e) => setVoyageKey(e.target.value)}
                placeholder="Enter new Voyage API key"
              />
              <button style={styles.button} onClick={() => saveApiKey("voyage", voyageKey)}>
                Save
              </button>
            </div>
          </div>

          <div style={styles.field}>
            <label style={styles.label}>
              Gemini API Key {geminiMasked && <span style={styles.masked}>Current: {geminiMasked}</span>}
            </label>
            <div style={styles.keyRow}>
              <input
                type="password"
                style={styles.input}
                value={geminiKey}
                onChange={(e) => setGeminiKey(e.target.value)}
                placeholder="Enter new Gemini API key"
              />
              <button style={styles.button} onClick={() => saveApiKey("gemini", geminiKey)}>
                Save
              </button>
            </div>
          </div>

          {keySaveMsg && <div style={{ ...styles.msg, color: "#6cffa0" }}>{keySaveMsg}</div>}
        </section>

        {/* General Section */}
        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>General</h2>
          <div style={styles.field}>
            <label style={styles.label}>LLM Provider</label>
            <div style={styles.readOnly}>
              {allSettings["llm.provider"] || "anthropic"} (from config)
            </div>
          </div>
          <div style={styles.field}>
            <label style={styles.label}>LLM Model</label>
            <div style={styles.readOnly}>
              {allSettings["llm.model"] || "claude-sonnet-4-20250514"} (from config)
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: {
    display: "flex",
    flexDirection: "column",
    height: "100vh",
    maxWidth: "800px",
    margin: "0 auto",
  },
  header: {
    display: "flex",
    alignItems: "center",
    justifyContent: "space-between",
    padding: "16px 24px",
    borderBottom: "1px solid #1e1e2e",
  },
  backLink: {
    color: "#6c8aff",
    textDecoration: "none",
    fontSize: "0.9rem",
  },
  title: {
    fontSize: "1.5rem",
    fontWeight: 700,
    margin: 0,
  },
  content: {
    flex: 1,
    overflowY: "auto",
    padding: "24px",
    display: "flex",
    flexDirection: "column",
    gap: "32px",
  },
  section: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "24px",
    border: "1px solid #1e1e2e",
  },
  sectionTitle: {
    fontSize: "1.1rem",
    fontWeight: 600,
    color: "#6c8aff",
    margin: "0 0 20px 0",
  },
  field: {
    marginBottom: "16px",
  },
  label: {
    display: "block",
    fontSize: "0.85rem",
    color: "#8888a0",
    marginBottom: "6px",
    fontWeight: 500,
  },
  select: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
  },
  input: {
    flex: 1,
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "10px 12px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    outline: "none",
  },
  modelDetails: {
    display: "flex",
    gap: "16px",
    flexWrap: "wrap" as const,
    fontSize: "0.8rem",
    color: "#8888a0",
    marginBottom: "16px",
    padding: "8px 12px",
    backgroundColor: "#1e1e2e",
    borderRadius: "8px",
  },
  radioGroup: {
    display: "flex",
    gap: "20px",
  },
  radioLabel: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
    color: "#e0e0e8",
    fontSize: "0.95rem",
    cursor: "pointer",
  },
  radio: {
    accentColor: "#6c8aff",
  },
  button: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  keyRow: {
    display: "flex",
    gap: "8px",
  },
  masked: {
    color: "#6c8aff",
    fontSize: "0.8rem",
    marginLeft: "8px",
  },
  warning: {
    backgroundColor: "#2a2a1a",
    border: "1px solid #aa8800",
    borderRadius: "8px",
    padding: "12px 16px",
    color: "#ffcc44",
    fontSize: "0.85rem",
    marginBottom: "16px",
  },
  msg: {
    marginTop: "12px",
    fontSize: "0.85rem",
  },
  readOnly: {
    color: "#8888a0",
    fontSize: "0.95rem",
    padding: "10px 12px",
    backgroundColor: "#1e1e2e",
    borderRadius: "8px",
  },
};
