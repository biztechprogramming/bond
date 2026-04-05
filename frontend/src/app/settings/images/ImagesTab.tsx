"use client";
import React, { useState, useEffect } from "react";
import { useSettings, useProviderApiKeys } from "@/hooks/useSpacetimeDB";
import { getConnection } from "@/lib/spacetimedb-client";

const PROVIDERS = ["openai", "replicate", "comfyui"] as const;

const MODELS: Record<string, string[]> = {
  openai: ["gpt-image-1", "dall-e-3", "dall-e-2"],
  replicate: ["black-forest-labs/flux-1.1-pro", "stability-ai/sdxl"],
  comfyui: [],
};

const RESOLUTIONS = ["256x256", "512x512", "1024x1024", "1536x1024"];
const QUALITIES = ["standard", "hd"];
const STYLES = ["natural", "vivid"];

export default function ImagesTab() {
  const settingsRows = useSettings();
  const allSettings = Object.fromEntries(settingsRows.map((s) => [s.key, s.value]));
  const providerApiKeys = useProviderApiKeys();
  const [saveMsg, setSaveMsg] = useState("");
  const [replicateKey, setReplicateKey] = useState("");

  const provider = allSettings["image.provider"] || "openai";
  const model = allSettings["image.model"] || MODELS[provider]?.[0] || "";
  const resolution = allSettings["image.resolution"] || "1024x1024";
  const quality = allSettings["image.quality"] || "standard";
  const style = allSettings["image.style"] || "natural";
  const budgetDaily = allSettings["image.budget_daily"] || "";
  const outputDir = allSettings["image.output_dir"] || ".bond/images";
  const comfyuiUrl = allSettings["image.comfyui_url"] || "";

  const setSetting = (key: string, value: string) => {
    const conn = getConnection();
    if (!conn) return;
    conn.reducers.setSetting({ key, value, keyType: "string" });
  };

  const handleSave = () => {
    setSaveMsg("Settings saved.");
    setTimeout(() => setSaveMsg(""), 3000);
  };

  const saveApiKey = () => {
    if (!replicateKey.trim()) return;
    const conn = getConnection();
    if (!conn) return;
    conn.reducers.setProviderApiKey({
      providerId: "replicate",
      encryptedValue: replicateKey.trim(),
      keyType: "image.api_key",
      createdAt: BigInt(0),
      updatedAt: BigInt(0),
    });
    setReplicateKey("");
    setSaveMsg("API key saved.");
  };

  const maskedReplicateKey = (() => {
    const found = providerApiKeys.find(
      (k) => k.providerId === "replicate" && k.keyType === "image.api_key"
    );
    if (!found?.encryptedValue) return "";
    const v = found.encryptedValue;
    return v.length <= 8 ? "••••••••" : v.slice(0, 7) + "••••••••" + v.slice(-4);
  })();

  // Compute daily spend placeholder (we don't have real data, so just show 0)
  const todaySpend = 0;
  const budgetNum = parseFloat(budgetDaily) || 0;
  const spendPct = budgetNum > 0 ? Math.min(100, (todaySpend / budgetNum) * 100) : 0;

  return (
    <section style={s.section}>
      <h2 style={s.sectionTitle}>Image Generation</h2>

      {/* Provider */}
      <div style={s.field}>
        <label style={s.label}>Provider</label>
        <select
          style={s.select}
          value={provider}
          onChange={(e) => {
            setSetting("image.provider", e.target.value);
            const defaultModel = MODELS[e.target.value]?.[0] || "";
            if (defaultModel) setSetting("image.model", defaultModel);
          }}
        >
          {PROVIDERS.map((p) => (
            <option key={p} value={p}>
              {p === "openai" ? "OpenAI" : p === "replicate" ? "Replicate" : "ComfyUI"}
            </option>
          ))}
        </select>
      </div>

      {/* Model */}
      <div style={s.field}>
        <label style={s.label}>Model</label>
        {provider === "comfyui" ? (
          <input
            style={s.input}
            value={model}
            onChange={(e) => setSetting("image.model", e.target.value)}
            placeholder="Workflow name"
          />
        ) : (
          <select
            style={s.select}
            value={model}
            onChange={(e) => setSetting("image.model", e.target.value)}
          >
            {(MODELS[provider] || []).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        )}
      </div>

      {/* Resolution */}
      <div style={s.field}>
        <label style={s.label}>Default Resolution</label>
        <div style={s.radioGroup}>
          {RESOLUTIONS.map((r) => (
            <label key={r} style={s.radioLabel}>
              <input
                type="radio"
                name="resolution"
                value={r}
                checked={resolution === r}
                onChange={() => setSetting("image.resolution", r)}
                style={s.radio}
              />
              {r}
            </label>
          ))}
        </div>
      </div>

      {/* Quality */}
      <div style={s.field}>
        <label style={s.label}>Quality</label>
        <div style={s.radioGroup}>
          {QUALITIES.map((q) => (
            <label key={q} style={s.radioLabel}>
              <input
                type="radio"
                name="quality"
                value={q}
                checked={quality === q}
                onChange={() => setSetting("image.quality", q)}
                style={s.radio}
              />
              {q.charAt(0).toUpperCase() + q.slice(1)}
            </label>
          ))}
        </div>
      </div>

      {/* Style (OpenAI only) */}
      {provider === "openai" && (
        <div style={s.field}>
          <label style={s.label}>Style</label>
          <div style={s.radioGroup}>
            {STYLES.map((st) => (
              <label key={st} style={s.radioLabel}>
                <input
                  type="radio"
                  name="style"
                  value={st}
                  checked={style === st}
                  onChange={() => setSetting("image.style", st)}
                  style={s.radio}
                />
                {st.charAt(0).toUpperCase() + st.slice(1)}
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Budget */}
      <div style={{ ...s.field, borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#1e1e2e", paddingTop: "16px", marginTop: "8px" }}>
        <h3 style={{ color: "#e0e0e8", fontSize: "0.95rem", marginBottom: "12px" }}>Budget Controls</h3>
        <label style={s.label}>Daily Budget Limit ($)</label>
        <input
          type="number"
          style={{ ...s.input, width: "120px" }}
          value={budgetDaily}
          onChange={(e) => setSetting("image.budget_daily", e.target.value)}
          placeholder="e.g. 5.00"
          min={0}
          step={0.5}
        />
        {budgetNum > 0 && (
          <div style={{ marginTop: "8px" }}>
            <div style={{ fontSize: "0.8rem", color: "#8888a0", marginBottom: "4px" }}>
              Today&apos;s spend: ${todaySpend.toFixed(2)} / ${budgetNum.toFixed(2)}
            </div>
            <div style={{ width: "200px", height: "6px", backgroundColor: "#1e1e2e", borderRadius: "3px", overflow: "hidden" }}>
              <div style={{ width: `${spendPct}%`, height: "100%", backgroundColor: spendPct > 80 ? "#ff6c8a" : "#6cffa0", borderRadius: "3px", transition: "width 0.3s" }} />
            </div>
          </div>
        )}
      </div>

      {/* Provider-specific */}
      {provider === "replicate" && (
        <div style={s.field}>
          <label style={s.label}>
            Replicate API Key{" "}
            {maskedReplicateKey && <span style={{ color: "#6c8aff", fontSize: "0.8rem", marginLeft: "8px" }}>Current: {maskedReplicateKey}</span>}
          </label>
          <div style={{ display: "flex", gap: "8px" }}>
            <input
              type="password"
              style={s.input}
              value={replicateKey}
              onChange={(e) => setReplicateKey(e.target.value)}
              placeholder="r8_..."
            />
            <button style={s.button} onClick={saveApiKey}>
              Save
            </button>
          </div>
        </div>
      )}

      {provider === "comfyui" && (
        <div style={s.field}>
          <label style={s.label}>ComfyUI Server URL</label>
          <input
            style={s.input}
            value={comfyuiUrl}
            onChange={(e) => setSetting("image.comfyui_url", e.target.value)}
            placeholder="http://localhost:8188"
          />
          <div style={{ marginTop: "6px", fontSize: "0.78rem", color: comfyuiUrl ? "#6cffa0" : "#5a5a6e" }}>
            {comfyuiUrl ? "URL configured" : "Not configured"}
          </div>
        </div>
      )}

      {/* Output */}
      <div style={s.field}>
        <label style={s.label}>Output Directory</label>
        <input
          style={s.input}
          value={outputDir}
          onChange={(e) => setSetting("image.output_dir", e.target.value)}
        />
      </div>

      <button style={s.button} onClick={handleSave}>
        Save Settings
      </button>
      {saveMsg && <div style={{ marginTop: "12px", fontSize: "0.85rem", color: "#6cffa0" }}>{saveMsg}</div>}
    </section>
  );
}

const s: Record<string, React.CSSProperties> = {
  section: { backgroundColor: "#12121a", borderRadius: "12px", padding: "24px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e", overflow: "visible", flexShrink: 0 },
  sectionTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: "0 0 20px 0" },
  field: { marginBottom: "16px" },
  label: { display: "block", fontSize: "0.85rem", color: "#8888a0", marginBottom: "6px", fontWeight: 500 },
  select: { width: "100%", backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.95rem", outline: "none" },
  input: { flex: 1, backgroundColor: "#1e1e2e", borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e", borderRadius: "8px", padding: "10px 12px", color: "#e0e0e8", fontSize: "0.95rem", outline: "none" },
  radioGroup: { display: "flex", gap: "20px", flexWrap: "wrap" as const },
  radioLabel: { display: "flex", alignItems: "center", gap: "6px", color: "#e0e0e8", fontSize: "0.95rem", cursor: "pointer" },
  radio: { accentColor: "#6c8aff" },
  button: { backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px", padding: "10px 20px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer" },
};
