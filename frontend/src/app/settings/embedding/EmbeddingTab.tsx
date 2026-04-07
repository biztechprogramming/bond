"use client";

import React, { useEffect, useState, useMemo } from "react";
import { s } from "../styles";
import { useEmbeddingModels, useSettingsMap, callReducer } from "@/hooks/useSpacetimeDB";
import type { EmbeddingModelRow } from "@/lib/spacetimedb-client";

export default function EmbeddingTab() {
  const models = useEmbeddingModels();
  const settingsMap = useSettingsMap();

  // Current embedding settings from the settings table
  const currentModel = settingsMap["embedding.model"] || "";
  const currentDimension = Number(settingsMap["embedding.output_dimension"] || "0");
  const currentMode = settingsMap["embedding.execution_mode"] || "auto";

  const [selectedModel, setSelectedModel] = useState("");
  const [selectedDimension, setSelectedDimension] = useState(0);
  const [selectedMode, setSelectedMode] = useState("auto");
  const [warning, setWarning] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState("");

  // Sync local state from settings when they load
  useEffect(() => {
    if (currentModel) setSelectedModel(currentModel);
    if (currentDimension) setSelectedDimension(currentDimension);
    if (currentMode) setSelectedMode(currentMode);
  }, [currentModel, currentDimension, currentMode]);

  // Parse supportedDimensions (stored as JSON string) for each model
  const parseDimensions = (m: EmbeddingModelRow): number[] => {
    try {
      return JSON.parse(m.supportedDimensions);
    } catch {
      return [m.maxDimension];
    }
  };

  const activeModel = useMemo(
    () => models.find((m) => m.modelName === selectedModel),
    [models, selectedModel]
  );

  const activeDimensions = useMemo(
    () => (activeModel ? parseDimensions(activeModel) : []),
    [activeModel]
  );

  // Warn on family change
  useEffect(() => {
    if (!activeModel || !currentModel) { setWarning(""); return; }
    const cur = models.find((m) => m.modelName === currentModel);
    setWarning(cur && cur.family !== activeModel.family
      ? `Switching from ${cur.family} to ${activeModel.family} requires re-embedding.`
      : "");
  }, [selectedModel, currentModel, activeModel, models]);

  // Auto-fix dimension if not supported by new model
  useEffect(() => {
    if (activeDimensions.length > 0 && !activeDimensions.includes(selectedDimension))
      setSelectedDimension(activeDimensions[activeDimensions.length - 1]);
  }, [selectedModel, activeDimensions, selectedDimension]);

  const availableModes = useMemo(() => {
    if (!activeModel) return [];
    const m: string[] = [];
    if (activeModel.supportsLocal) m.push("local");
    if (activeModel.supportsApi) m.push("api");
    if (activeModel.supportsLocal && activeModel.supportsApi) m.push("auto");
    return m;
  }, [activeModel]);

  // Auto-fix mode if not available
  useEffect(() => {
    if (availableModes.length > 0 && !availableModes.includes(selectedMode))
      setSelectedMode(availableModes[0]);
  }, [selectedModel, availableModes, selectedMode]);

  const saveEmbedding = () => {
    setSaving(true);
    setSaveMsg("");
    const ok = callReducer((conn) => {
      conn.reducers.setSetting({ key: "embedding.model", value: selectedModel, keyType: "string" });
      conn.reducers.setSetting({ key: "embedding.output_dimension", value: String(selectedDimension), keyType: "string" });
      conn.reducers.setSetting({ key: "embedding.execution_mode", value: selectedMode, keyType: "string" });
    });
    if (ok) {
      setSaveMsg("Saved.");
    } else {
      setSaveMsg("Error: No SpacetimeDB connection.");
    }
    setSaving(false);
  };

  if (models.length === 0) {
    return (
      <section style={s.section}>
        <h2 style={s.sectionTitle}>Embedding Model</h2>
        <p style={s.helpText}>No embedding models available. Models will appear here once they are loaded into SpacetimeDB.</p>
      </section>
    );
  }

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
          {models.map((m) => <option key={m.modelName} value={m.modelName}>{m.modelName} ({m.family})</option>)}
        </select>
      </div>
      {activeModel && (
        <div style={s.modelDetails}>
          <span>Family: {activeModel.family}</span>
          <span>Provider: {activeModel.provider}</span>
          <span>Local: {activeModel.supportsLocal ? "Yes" : "No"}</span>
          <span>API: {activeModel.supportsApi ? "Yes" : "No"}</span>
          <span>Max dim: {activeModel.maxDimension}</span>
        </div>
      )}
      <div style={s.field}>
        <label style={s.label}>Dimension</label>
        <p style={s.helpText}>
          The number of dimensions in each embedding vector. Higher dimensions capture more nuance but use more storage and compute. 1024 is a good default for most use cases.
        </p>
        <select style={s.select} value={selectedDimension} onChange={(e) => setSelectedDimension(Number(e.target.value))}>
          {activeDimensions.map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
      </div>
      <div style={s.field}>
        <label style={s.label}>Execution Mode</label>
        <p style={s.helpText}>
          How embeddings are generated. &quot;local&quot; runs the model on this machine (free, private, but slower). &quot;api&quot; calls the Voyage AI API (fast, requires API key). &quot;auto&quot; tries API providers first, falls back to local.
        </p>
        <div style={s.radioGroup}>
          {availableModes.map((mode) => (
            <label key={mode} style={s.radioLabel}>
              <input type="radio" name="exec_mode" value={mode} checked={selectedMode === mode} onChange={() => setSelectedMode(mode)} style={s.radio} />
              {mode}
            </label>
          ))}
        </div>
      </div>
      <button style={{ ...s.button, opacity: saving ? 0.5 : 1 }} onClick={saveEmbedding} disabled={saving}>
        {saving ? "Saving..." : "Save Embedding Settings"}
      </button>
      {saveMsg && <div style={{ ...s.msg, color: saveMsg.startsWith("Error") ? "#ff6c8a" : "#6cffa0" }}>{saveMsg}</div>}
    </section>
  );
}
