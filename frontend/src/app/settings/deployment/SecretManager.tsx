import React, { useEffect, useState, useCallback, useRef } from "react";
import { GATEWAY_API } from "@/lib/config";

interface SecretManagerProps {
  environment: string;
  onBack: () => void;
}

interface Secret {
  key: string;
  value: string;
  source: "manual" | "discovered";
  created_at?: string;
  updated_at?: string;
}

export default function SecretManager({ environment, onBack }: SecretManagerProps) {
  const [secrets, setSecrets] = useState<Secret[]>([]);
  const [discovered, setDiscovered] = useState<string[]>([]);
  const [revealedKeys, setRevealedKeys] = useState<Set<string>>(new Set());
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [addMode, setAddMode] = useState(false);
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [msg, setMsg] = useState("");
  const timersRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());

  const fetchSecrets = useCallback(async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/secrets/${environment}`);
      if (res.ok) {
        const data = await res.json();
        setSecrets(data.secrets || []);
        setDiscovered(data.discovered_unmanaged || []);
      }
    } catch { /* ignore */ }
  }, [environment]);

  useEffect(() => { fetchSecrets(); }, [fetchSecrets]);

  useEffect(() => {
    return () => { timersRef.current.forEach((t) => clearTimeout(t)); };
  }, []);

  const revealSecret = (key: string) => {
    setRevealedKeys((prev) => new Set(prev).add(key));
    const existing = timersRef.current.get(key);
    if (existing) clearTimeout(existing);
    const timer = setTimeout(() => {
      setRevealedKeys((prev) => { const next = new Set(prev); next.delete(key); return next; });
      timersRef.current.delete(key);
    }, 10000);
    timersRef.current.set(key, timer);
  };

  const saveSecret = async (key: string, value: string) => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/secrets/${environment}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, value }),
      });
      if (res.ok) {
        setEditingKey(null);
        setEditValue("");
        await fetchSecrets();
      } else { setMsg("Failed to save secret"); }
    } catch { setMsg("Failed to save secret"); }
  };

  const deleteSecret = async (key: string) => {
    try {
      await fetch(`${GATEWAY_API}/deployments/secrets/${environment}/${encodeURIComponent(key)}`, { method: "DELETE" });
      await fetchSecrets();
    } catch { setMsg("Failed to delete secret"); }
  };

  const addSecret = async () => {
    if (!newKey.trim()) { setMsg("Key is required"); return; }
    await saveSecret(newKey.trim(), newValue);
    setAddMode(false);
    setNewKey("");
    setNewValue("");
  };

  const importEnv = async () => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".env,.env.*";
    input.onchange = async (e) => {
      const file = (e.target as HTMLInputElement).files?.[0];
      if (!file) return;
      const text = await file.text();
      try {
        const res = await fetch(`${GATEWAY_API}/deployments/secrets/${environment}/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ content: text }),
        });
        if (res.ok) {
          setMsg("Imported successfully");
          await fetchSecrets();
        } else { setMsg("Import failed"); }
      } catch { setMsg("Import failed"); }
    };
    input.click();
  };

  const rotateEncryption = async () => {
    try {
      const res = await fetch(`${GATEWAY_API}/deployments/secrets/${environment}/rotate`, { method: "POST" });
      setMsg(res.ok ? "Encryption rotated" : "Rotation failed");
    } catch { setMsg("Rotation failed"); }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ fontSize: "1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
          Secrets — {environment}
        </h3>
        <div style={{ display: "flex", gap: "8px" }}>
          <button style={styles.primaryBtn} onClick={() => setAddMode(true)}>Add Secret</button>
          <button style={styles.secondaryBtn} onClick={importEnv}>Import .env</button>
          <button style={styles.secondaryBtn} onClick={rotateEncryption}>Rotate Encryption</button>
          <button style={styles.secondaryBtn} onClick={onBack}>Back</button>
        </div>
      </div>

      {/* Secrets table */}
      <div style={{ backgroundColor: "#1a1a2e", borderRadius: "8px", border: "1px solid #3a3a4e", overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "200px 1fr 100px 160px", padding: "8px 14px", borderBottom: "1px solid #3a3a4e" }}>
          <span style={styles.header}>Key</span>
          <span style={styles.header}>Value</span>
          <span style={styles.header}>Source</span>
          <span style={styles.header}>Actions</span>
        </div>
        {secrets.length === 0 && (
          <div style={{ color: "#8888a0", fontSize: "0.85rem", padding: "16px", textAlign: "center" }}>No secrets configured.</div>
        )}
        {secrets.map((s) => (
          <div key={s.key} style={{ display: "grid", gridTemplateColumns: "200px 1fr 100px 160px", padding: "8px 14px", borderBottom: "1px solid #2a2a3e", alignItems: "center" }}>
            <span style={{ color: "#e0e0e8", fontSize: "0.85rem", fontFamily: "monospace" }}>{s.key}</span>
            <span style={{ color: "#8888a0", fontSize: "0.85rem", fontFamily: "monospace" }}>
              {editingKey === s.key ? (
                <div style={{ display: "flex", gap: "4px" }}>
                  <input style={styles.input} value={editValue} onChange={(e) => setEditValue(e.target.value)} />
                  <button style={styles.tinyBtn} onClick={() => saveSecret(s.key, editValue)}>Save</button>
                  <button style={styles.tinyBtn} onClick={() => setEditingKey(null)}>Cancel</button>
                </div>
              ) : revealedKeys.has(s.key) ? s.value : "●●●●●●●●"}
            </span>
            <span style={{ fontSize: "0.75rem", color: s.source === "discovered" ? "#ffcc6c" : "#8888a0" }}>{s.source}</span>
            <div style={{ display: "flex", gap: "4px" }}>
              {!revealedKeys.has(s.key) && editingKey !== s.key && (
                <button style={styles.tinyBtn} onClick={() => revealSecret(s.key)}>Reveal</button>
              )}
              <button style={styles.tinyBtn} onClick={() => { setEditingKey(s.key); setEditValue(s.value); }}>Edit</button>
              <button style={{ ...styles.tinyBtn, color: "#ff6c8a" }} onClick={() => deleteSecret(s.key)}>Delete</button>
            </div>
          </div>
        ))}
      </div>

      {/* Add secret inline */}
      {addMode && (
        <div style={{ backgroundColor: "#1a1a2e", borderRadius: "8px", border: "1px solid #6c8aff", padding: "12px", display: "flex", gap: "8px", alignItems: "flex-end" }}>
          <label style={styles.label}>Key<input style={styles.input} value={newKey} onChange={(e) => setNewKey(e.target.value)} /></label>
          <label style={styles.label}>Value<input style={styles.input} value={newValue} onChange={(e) => setNewValue(e.target.value)} /></label>
          <button style={styles.primaryBtn} onClick={addSecret}>Add</button>
          <button style={styles.secondaryBtn} onClick={() => setAddMode(false)}>Cancel</button>
        </div>
      )}

      {/* Discovered but not managed */}
      {discovered.length > 0 && (
        <div style={{ backgroundColor: "#1a1a2e", borderRadius: "8px", border: "1px solid #ffcc6c33", padding: "12px" }}>
          <div style={{ color: "#ffcc6c", fontSize: "0.85rem", fontWeight: 600, marginBottom: "8px" }}>
            Discovered but not managed ({discovered.length})
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px" }}>
            {discovered.map((key) => (
              <span key={key} style={{
                backgroundColor: "#2a2a3e", borderRadius: "4px", padding: "4px 8px",
                fontSize: "0.8rem", color: "#e0e0e8", fontFamily: "monospace",
              }}>
                {key}
              </span>
            ))}
          </div>
        </div>
      )}

      {msg && <div style={{ fontSize: "0.85rem", color: msg.includes("fail") || msg.includes("Failed") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  primaryBtn: { backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" },
  secondaryBtn: { backgroundColor: "#2a2a3e", color: "#e0e0e8", border: "1px solid #3a3a4e", borderRadius: "8px", padding: "8px 16px", fontSize: "0.85rem", cursor: "pointer" },
  tinyBtn: { backgroundColor: "transparent", color: "#8888a0", border: "1px solid #3a3a4e", borderRadius: "4px", padding: "2px 8px", fontSize: "0.75rem", cursor: "pointer" },
  header: { color: "#8888a0", fontSize: "0.75rem", fontWeight: 600, textTransform: "uppercase" as const },
  input: { backgroundColor: "#2a2a3e", color: "#e0e0e8", border: "1px solid #3a3a4e", borderRadius: "6px", padding: "6px 10px", fontSize: "0.85rem" },
  label: { display: "flex", flexDirection: "column" as const, gap: "4px", color: "#8888a0", fontSize: "0.8rem" },
};
