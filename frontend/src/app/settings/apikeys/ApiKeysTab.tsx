"use client";

import React, { useState } from "react";
import { useProviderApiKeys } from "@/hooks/useSpacetimeDB";
import { getConnection } from "@/lib/spacetimedb-client";
import { s } from "../styles";

export default function ApiKeysTab() {
  const providerApiKeys = useProviderApiKeys();

  const [anthropicKey, setAnthropicKey] = useState("");
  const [openaiKey, setOpenaiKey] = useState("");
  const [googleKey, setGoogleKey] = useState("");
  const [voyageKey, setVoyageKey] = useState("");
  const [geminiKey, setGeminiKey] = useState("");
  const [replicateKey, setReplicateKey] = useState("");
  const [keySaveMsg, setKeySaveMsg] = useState("");

  const saveKey = async (fullKey: string, value: string, clearFn: (v: string) => void) => {
    if (!value.trim()) return;
    setKeySaveMsg("");
    try {
      const conn = getConnection();
      if (!conn) { setKeySaveMsg("Not connected."); return; }
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
    const parts = key.split(".");
    const providerId = parts[parts.length - 1];
    const keyType = parts.slice(0, parts.length - 1).join(".");
    const found = providerApiKeys.find(k => k.providerId === providerId && k.keyType === keyType);
    if (!found || !found.encryptedValue) return "";
    const v = found.encryptedValue;
    if (v.length <= 8) return "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022";
    return v.slice(0, 7) + "\u2022\u2022\u2022\u2022\u2022\u2022\u2022\u2022" + v.slice(-4);
  };

  const renderKeySection = (title: string, keys: { label: string; key: string; state: string; set: (v: string) => void; placeholder: string }[]) => (
    <>
      <h3 style={{ color: "#e0e0e8", fontSize: "0.95rem", margin: title === "LLM Providers" ? "0 0 12px" : "24px 0 12px" }}>{title}</h3>
      {keys.map(({ label, key, state, set, placeholder }) => (
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
    </>
  );

  return (
    <section style={s.section}>
      <h2 style={s.sectionTitle}>API Keys</h2>
      <p style={{ color: "#8888a0", fontSize: "0.85rem", marginBottom: "20px" }}>All keys are encrypted at rest.</p>

      {renderKeySection("LLM Providers", [
        { label: "Anthropic", key: "llm.api_key.anthropic", state: anthropicKey, set: setAnthropicKey, placeholder: "sk-ant-..." },
        { label: "OpenAI", key: "llm.api_key.openai", state: openaiKey, set: setOpenaiKey, placeholder: "sk-..." },
        { label: "Google", key: "llm.api_key.google", state: googleKey, set: setGoogleKey, placeholder: "Google API key" },
      ])}

      {renderKeySection("Embedding Providers", [
        { label: "Voyage AI", key: "embedding.api_key.voyage", state: voyageKey, set: setVoyageKey, placeholder: "Voyage API key" },
        { label: "Gemini", key: "embedding.api_key.gemini", state: geminiKey, set: setGeminiKey, placeholder: "Gemini API key" },
      ])}

      {renderKeySection("Image Providers", [
        { label: "Replicate", key: "image.api_key.replicate", state: replicateKey, set: setReplicateKey, placeholder: "r8_..." },
      ])}

      {keySaveMsg && <div style={{ ...s.msg, color: "#6cffa0" }}>{keySaveMsg}</div>}
    </section>
  );
}
