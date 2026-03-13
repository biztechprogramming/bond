import React, { useState } from "react";
import { BACKEND_API } from "@/lib/config";

interface Props {
  initialYaml?: string;
  repoUrl?: string;
}

interface ValidationResult {
  valid: boolean;
  errors?: string[];
  warnings?: string[];
}

export default function PipelineYamlEditor({ initialYaml = "", repoUrl }: Props) {
  const [yaml, setYaml] = useState(initialYaml);
  const [validation, setValidation] = useState<ValidationResult | null>(null);
  const [validating, setValidating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [msg, setMsg] = useState("");
  const [copied, setCopied] = useState(false);

  const handleValidate = async () => {
    setValidating(true);
    setValidation(null);
    setMsg("");
    try {
      const res = await fetch(`${BACKEND_API}/deployments/validate-yaml`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yaml }),
      });
      if (res.ok) {
        setValidation(await res.json());
      } else if (res.status === 404) {
        setMsg("Validation API not available yet. You can still save.");
      } else {
        setMsg("Validation failed.");
      }
    } catch {
      setMsg("Validation API not available yet. You can still save.");
    }
    setValidating(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setMsg("");
    try {
      const res = await fetch(`${BACKEND_API}/deployments/pipeline-yaml`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yaml, repo: repoUrl }),
      });
      if (res.ok) {
        setMsg("Pipeline YAML saved.");
      } else if (res.status === 404) {
        setMsg("Save API not available yet.");
      } else {
        setMsg("Failed to save pipeline YAML.");
      }
    } catch {
      setMsg("Save API not available yet.");
    }
    setSaving(false);
  };

  const handleLoad = async () => {
    if (!repoUrl) return;
    setLoading(true);
    setMsg("");
    try {
      const res = await fetch(`${BACKEND_API}/deployments/pipeline-yaml?repo=${encodeURIComponent(repoUrl)}`);
      if (res.ok) {
        const data = await res.json();
        setYaml(data.yaml || "");
        setMsg("Loaded from repository.");
      } else if (res.status === 404) {
        setMsg("No .bond/deploy.yml found in repository.");
      } else {
        setMsg("Failed to load pipeline YAML.");
      }
    } catch {
      setMsg("Load API not available yet.");
    }
    setLoading(false);
  };

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(yaml);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setMsg("Failed to copy to clipboard.");
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.header}>
        <h4 style={styles.title}>Pipeline YAML</h4>
        <span style={styles.filename}>.bond/deploy.yml</span>
      </div>

      <textarea
        style={styles.editor}
        value={yaml}
        onChange={(e) => { setYaml(e.target.value); setValidation(null); }}
        placeholder={PLACEHOLDER}
        spellCheck={false}
      />

      {validation && (
        <div style={styles.validationBox}>
          {validation.valid && (
            <div style={{ color: "#6cffa0", fontSize: "0.8rem" }}>Pipeline YAML is valid.</div>
          )}
          {validation.errors && validation.errors.map((err, i) => (
            <div key={`e-${i}`} style={{ color: "#ff6c8a", fontSize: "0.8rem" }}>{err}</div>
          ))}
          {validation.warnings && validation.warnings.map((warn, i) => (
            <div key={`w-${i}`} style={{ color: "#ffcc44", fontSize: "0.8rem" }}>{warn}</div>
          ))}
        </div>
      )}

      {msg && (
        <div style={{
          fontSize: "0.8rem",
          color: msg.includes("Failed") || msg.includes("failed") || msg.includes("not available")
            ? "#ffcc44" : "#6cffa0",
        }}>
          {msg}
        </div>
      )}

      <div style={styles.actions}>
        <button style={styles.primaryBtn} onClick={handleValidate} disabled={validating || !yaml.trim()}>
          {validating ? "Validating..." : "Validate"}
        </button>
        <button style={styles.primaryBtn} onClick={handleSave} disabled={saving || !yaml.trim()}>
          {saving ? "Saving..." : "Save"}
        </button>
        <button style={styles.secondaryBtn} onClick={handleCopy} disabled={!yaml.trim()}>
          {copied ? "Copied!" : "Copy"}
        </button>
        {repoUrl && (
          <button style={styles.secondaryBtn} onClick={handleLoad} disabled={loading}>
            {loading ? "Loading..." : "Load from Repo"}
          </button>
        )}
      </div>
    </div>
  );
}

const PLACEHOLDER = `# .bond/deploy.yml
pipeline: myapp

on:
  push:
    branches: [main]
  manual: true

steps:
  - name: test
    image: node:22
    commands:
      - npm ci
      - npm test

  - name: build
    image: node:22
    commands:
      - npm ci
      - npm run build
    depends_on: [test]

  - name: deploy
    image: bond-deploy-agent
    commands:
      - ./scripts/deploy.sh
    secrets: [DEPLOY_KEY]
    depends_on: [build]
`;

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: "10px" },
  header: { display: "flex", alignItems: "center", gap: "10px" },
  title: { fontSize: "0.9rem", fontWeight: 600, color: "#8888a0", margin: 0 },
  filename: { fontSize: "0.75rem", color: "#5a5a6e", fontFamily: "monospace" },
  editor: {
    backgroundColor: "#0a0a12",
    color: "#e0e0e8",
    border: "1px solid #1e1e2e",
    borderRadius: "8px",
    padding: "14px",
    fontFamily: "monospace",
    fontSize: "0.8rem",
    lineHeight: "1.5",
    minHeight: "280px",
    resize: "vertical",
    outline: "none",
    tabSize: 2,
  },
  validationBox: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "6px",
    padding: "10px",
    display: "flex",
    flexDirection: "column",
    gap: "4px",
  },
  actions: { display: "flex", gap: "8px", flexWrap: "wrap" },
  primaryBtn: {
    backgroundColor: "#6c8aff",
    color: "#12121a",
    border: "none",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  secondaryBtn: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
};
