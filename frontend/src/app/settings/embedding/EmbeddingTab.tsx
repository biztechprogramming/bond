"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";
import { s } from "../styles";

const API_BASE = `${BACKEND_API}/settings`;

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

export default function EmbeddingTab() {
  const [models, setModels] = useState<EmbeddingModel[]>([]);
  const [current, setCurrent] = useState<EmbeddingCurrent | null>(null);
  const [selectedModel, setSelectedModel] = useState("");
  const [selectedDimension, setSelectedDimension] = useState(0);
  const [selectedMode, setSelectedMode] = useState("auto");
  const [warning, setWarning] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  const fetchSettings = useCallback(async () => {
    let modelsData: EmbeddingModel[] = [];
    try {
      const modelsRes = await apiFetch(`${API_BASE}/embedding/models`);
      if (modelsRes.ok) {
        modelsData = await modelsRes.json();
      }
    } catch { /* ignore */ }
    setModels(modelsData);

    try {
      const currentRes = await apiFetch(`${API_BASE}/embedding/current`);
      if (currentRes.ok) {
        const cur = await currentRes.json();
        setCurrent(cur);
        setSelectedModel(cur.model);
        setSelectedDimension(cur.dimension);
        setSelectedMode(cur.execution_mode);
      }
    } catch { /* ignore */ }
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
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedModel, activeModel]);

  const saveEmbedding = async () => {
    setSaving(true); setSaveMsg("");
    try {
      const res = await apiFetch(`${API_BASE}/embedding`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: selectedModel, dimension: selectedDimension, execution_mode: selectedMode }),
      });
      const data = await res.json();
      setSaveMsg(!res.ok ? `Error: ${data.detail}` : data.warning ? `Saved. Warning: ${data.warning}` : "Saved.");
      if (res.ok) await fetchSettings();
    } catch { setSaveMsg("Failed to save."); }
    setSaving(false);
  };

  return (
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
  );
}
