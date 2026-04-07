"use client";

import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API, apiFetch } from "@/lib/config";
import { useSettings } from "@/hooks/useSpacetimeDB";
import { getConnection } from "@/lib/spacetimedb-client";
import { s } from "../styles";

const API_BASE = `${BACKEND_API}/settings`;

interface LlmCurrent {
  provider: string;
  model: string;
  keys_set: Record<string, boolean>;
}

export default function LlmTab() {
  const settingsRows = useSettings();
  const allSettings = Object.fromEntries(settingsRows.map(r => [r.key, r.value]));
  const [llmCurrent, setLlmCurrent] = useState<LlmCurrent | null>(null);
  const [saveMsg, setSaveMsg] = useState("");

  const fetchLlm = useCallback(async () => {
    try {
      const res = await apiFetch(`${API_BASE}/llm/current`);
      if (res.ok) setLlmCurrent(await res.json());
    } catch { /* ignore */ }
  }, []);

  useEffect(() => { fetchLlm(); }, [fetchLlm]);

  return (
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
            <span key={p}>{p}: {set ? "\u2705" : "\u274C"}</span>
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

      {saveMsg && <div style={{ ...s.msg, color: "#6cffa0" }}>{saveMsg}</div>}
    </section>
  );
}
