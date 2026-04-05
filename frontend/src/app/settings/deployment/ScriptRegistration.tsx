import React, { useState, useRef, useCallback, useEffect } from "react";
import { GATEWAY_API , apiFetch } from "@/lib/config";

const TEMPLATES: Record<string, { label: string; script: string }> = {
  blank: {
    label: "Blank",
    script: `#!/usr/bin/env bash
set -euo pipefail

echo "Deploying to $BOND_DEPLOY_ENV"
`,
  },
  ssh: {
    label: "SSH Deploy",
    script: `#!/usr/bin/env bash
# meta:name: SSH Deploy
# meta:timeout: 300
# meta:dry_run: true
set -euo pipefail

SERVER="\${DEPLOY_SERVER:?Set DEPLOY_SERVER}"
BRANCH="\${DEPLOY_BRANCH:-main}"

echo "Deploying to $BOND_DEPLOY_ENV via SSH..."

if [ "\${BOND_DRY_RUN:-}" = "true" ]; then
  echo "[dry-run] Would pull $BRANCH on $SERVER"
  exit 0
fi

ssh deploy@"$SERVER" "cd /app && git fetch origin && git checkout $BRANCH && git pull"
ssh deploy@"$SERVER" "cd /app && sudo systemctl restart app"
echo "Deploy complete."
`,
  },
  docker: {
    label: "Docker Deploy",
    script: `#!/usr/bin/env bash
# meta:name: Docker Deploy
# meta:timeout: 600
# meta:dry_run: true
set -euo pipefail

IMAGE="\${DOCKER_IMAGE:?Set DOCKER_IMAGE}"
TAG="\${DEPLOY_TAG:-latest}"

echo "Building and deploying $IMAGE:$TAG..."

if [ "\${BOND_DRY_RUN:-}" = "true" ]; then
  echo "[dry-run] Would build and push $IMAGE:$TAG"
  exit 0
fi

docker build -t "$IMAGE:$TAG" .
docker push "$IMAGE:$TAG"
docker stop app || true
docker run -d --name app --rm -p 3000:3000 "$IMAGE:$TAG"
echo "Container running."
`,
  },
  migration: {
    label: "Database Migration",
    script: `#!/usr/bin/env bash
# meta:name: Database Migration
# meta:timeout: 120
# meta:dry_run: true
set -euo pipefail

DATABASE_URL="\${DATABASE_URL:?Set DATABASE_URL}"

echo "Running migration on $BOND_DEPLOY_ENV..."

if [ "\${BOND_DRY_RUN:-}" = "true" ]; then
  echo "[dry-run] Would run migrations"
  exit 0
fi

# Replace with your migration tool
# npx prisma migrate deploy
# python manage.py migrate
# bun run migrate:up

echo "Migration complete."
`,
  },
  kubernetes: {
    label: "Kubernetes Rollout",
    script: `#!/usr/bin/env bash
# meta:name: Kubernetes Rollout
# meta:timeout: 600
# meta:dry_run: true
set -euo pipefail

NAMESPACE="\${K8S_NAMESPACE:-default}"
DEPLOYMENT="\${K8S_DEPLOYMENT:?Set K8S_DEPLOYMENT}"
IMAGE="\${DOCKER_IMAGE:?Set DOCKER_IMAGE}"
TAG="\${DEPLOY_TAG:-latest}"

echo "Rolling out $DEPLOYMENT in $NAMESPACE..."

if [ "\${BOND_DRY_RUN:-}" = "true" ]; then
  kubectl set image "deployment/$DEPLOYMENT" "app=$IMAGE:$TAG" -n "$NAMESPACE" --dry-run=client
  exit 0
fi

kubectl set image "deployment/$DEPLOYMENT" "app=$IMAGE:$TAG" -n "$NAMESPACE"
kubectl rollout status "deployment/$DEPLOYMENT" -n "$NAMESPACE" --timeout=300s
echo "Rollout complete."
`,
  },
  s3: {
    label: "Static Site (S3)",
    script: `#!/usr/bin/env bash
# meta:name: Static Site Deploy
# meta:timeout: 300
# meta:dry_run: true
set -euo pipefail

S3_BUCKET="\${S3_BUCKET:?Set S3_BUCKET}"
BUILD_DIR="\${BUILD_DIR:-dist}"
CF_DISTRIBUTION="\${CF_DISTRIBUTION:-}"

echo "Deploying static site to s3://$S3_BUCKET..."

if [ "\${BOND_DRY_RUN:-}" = "true" ]; then
  echo "[dry-run] Would sync $BUILD_DIR to s3://$S3_BUCKET"
  exit 0
fi

aws s3 sync "$BUILD_DIR" "s3://$S3_BUCKET" --delete
if [ -n "$CF_DISTRIBUTION" ]; then
  aws cloudfront create-invalidation --distribution-id "$CF_DISTRIBUTION" --paths "/*"
fi
echo "Deploy complete."
`,
  },
};

function parseMetaFromScript(script: string): Record<string, string> {
  const meta: Record<string, string> = {};
  for (const line of script.split("\n")) {
    const match = line.match(/^#\s*meta:(\w+):\s*(.+)$/);
    if (match) meta[match[1]!] = match[2]!.trim();
  }
  return meta;
}

interface Props {
  onBack: () => void;
  onRegistered?: () => void;
}

export default function ScriptRegistration({ onBack, onRegistered }: Props) {
  const [scriptId, setScriptId] = useState("");
  const [version, setVersion] = useState("v1");
  const [name, setName] = useState("");
  const [script, setScript] = useState(TEMPLATES.blank.script);
  const [timeout, setTimeout_] = useState(300);
  const [dryRun, setDryRun] = useState(false);
  const [dependsOn, setDependsOn] = useState("");
  const [template, setTemplate] = useState("blank");

  const [syntaxValid, setSyntaxValid] = useState<boolean | null>(null);
  const [syntaxErrors, setSyntaxErrors] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState("");

  // Additional files
  const [additionalFiles, setAdditionalFiles] = useState<Record<string, string>>({});

  const debounceRef = useRef<ReturnType<typeof globalThis.setTimeout> | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const validateSyntax = useCallback((content: string) => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = globalThis.setTimeout(async () => {
      try {
        const res = await apiFetch(`${GATEWAY_API}/deployments/scripts/validate-syntax`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ script: content }),
        });
        const data = await res.json();
        setSyntaxValid(data.valid);
        setSyntaxErrors(data.errors || []);
      } catch {
        setSyntaxValid(null);
        setSyntaxErrors([]);
      }
    }, 500);
  }, []);

  useEffect(() => {
    if (script.trim()) validateSyntax(script);
  }, [script, validateSyntax]);

  const handleTemplateChange = (key: string) => {
    setTemplate(key);
    const t = TEMPLATES[key];
    if (!t) return;
    setScript(t.script);
    const meta = parseMetaFromScript(t.script);
    if (meta.name) setName(meta.name);
    if (meta.timeout) setTimeout_(parseInt(meta.timeout));
    if (meta.dry_run === "true") setDryRun(true);
  };

  const handleUploadDeploy = () => {
    fileInputRef.current?.click();
  };

  const handleFileRead = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const content = reader.result as string;
      setScript(content);
      const meta = parseMetaFromScript(content);
      if (meta.name && !name) setName(meta.name);
      if (meta.timeout) setTimeout_(parseInt(meta.timeout));
      if (meta.dry_run === "true") setDryRun(true);
    };
    reader.readAsText(file);
  };

  const handleAddFile = (filename: string, content: string) => {
    setAdditionalFiles((prev) => ({ ...prev, [filename]: content }));
  };

  const handleUploadAdditional = (targetName: string) => {
    const input = document.createElement("input");
    input.type = "file";
    input.onchange = () => {
      const file = input.files?.[0];
      if (!file) return;
      const reader = new FileReader();
      reader.onload = () => handleAddFile(targetName || file.name, reader.result as string);
      reader.readAsText(file);
    };
    input.click();
  };

  const submit = async (promoteAfter: boolean) => {
    if (!scriptId || !script.trim()) {
      setMsg("Script ID and deploy.sh content are required.");
      return;
    }
    setSubmitting(true);
    setMsg("");

    try {
      const files: Record<string, string> = {
        "deploy.sh": btoa(unescape(encodeURIComponent(script))),
      };
      for (const [fname, content] of Object.entries(additionalFiles)) {
        files[fname] = btoa(unescape(encodeURIComponent(content)));
      }

      const body: any = {
        script_id: scriptId,
        version,
        name: name || scriptId,
        timeout,
        dry_run: dryRun,
        depends_on: dependsOn ? dependsOn.split(",").map((s) => s.trim()).filter(Boolean) : [],
        files,
      };

      const res = await apiFetch(`${GATEWAY_API}/deployments/scripts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json();
        setMsg(`Registration failed: ${err.error}`);
        setSubmitting(false);
        return;
      }

      if (promoteAfter) {
        const promRes = await apiFetch(`${GATEWAY_API}/deployments/promote`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            script_id: scriptId,
            version,
            target_environments: ["dev"],
          }),
        });
        if (promRes.ok) {
          setMsg("Registered and promoted to dev.");
        } else {
          setMsg("Registered but promotion failed.");
        }
      } else {
        setMsg("Script registered successfully.");
      }

      onRegistered?.();
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.headerRow}>
        <h2 style={styles.title}>Register Deployment Script</h2>
        <button style={styles.secondaryButton} onClick={onBack}>Cancel</button>
      </div>

      {/* Template selector */}
      <div style={styles.fieldGroup}>
        <label style={styles.label}>Start from template</label>
        <select
          style={styles.select}
          value={template}
          onChange={(e) => handleTemplateChange(e.target.value)}
        >
          {Object.entries(TEMPLATES).map(([key, t]) => (
            <option key={key} value={key}>{t.label}</option>
          ))}
        </select>
      </div>

      {/* Identity */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Identity</span>
        <div style={styles.fieldRow}>
          <div style={styles.field}>
            <label style={styles.label}>Script ID</label>
            <input
              style={styles.input}
              value={scriptId}
              onChange={(e) => setScriptId(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
              placeholder="my-deploy-script"
            />
          </div>
          <div style={{ ...styles.field, maxWidth: 100 }}>
            <label style={styles.label}>Version</label>
            <input style={styles.input} value={version} onChange={(e) => setVersion(e.target.value)} />
          </div>
          <div style={styles.field}>
            <label style={styles.label}>Name</label>
            <input
              style={styles.input}
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Display name"
            />
          </div>
        </div>
      </div>

      {/* Editor */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>deploy.sh</span>
        <textarea
          style={styles.editor}
          value={script}
          onChange={(e) => setScript(e.target.value)}
          rows={15}
          spellCheck={false}
        />
        <div style={styles.editorFooter}>
          <button style={styles.secondaryButton} onClick={handleUploadDeploy}>Upload File</button>
          <input ref={fileInputRef} type="file" style={{ display: "none" }} onChange={handleFileRead} />
          <span style={{ fontSize: "0.8rem", color: syntaxValid === true ? "#6cffa0" : syntaxValid === false ? "#ff6c8a" : "#8888a0" }}>
            {syntaxValid === true && "Syntax: valid"}
            {syntaxValid === false && `Syntax: ${syntaxErrors[0] || "invalid"}`}
            {syntaxValid === null && ""}
          </span>
        </div>
        {syntaxValid === false && syntaxErrors.length > 1 && (
          <div style={{ fontSize: "0.75rem", color: "#ff6c8a", marginTop: 4 }}>
            {syntaxErrors.map((e, i) => <div key={i}>{e}</div>)}
          </div>
        )}
      </div>

      {/* Options */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Options</span>
        <div style={styles.fieldRow}>
          <div style={{ ...styles.field, maxWidth: 120 }}>
            <label style={styles.label}>Timeout (s)</label>
            <input
              style={styles.input}
              type="number"
              value={timeout}
              onChange={(e) => setTimeout_(parseInt(e.target.value) || 300)}
            />
          </div>
          <div style={{ ...styles.field, maxWidth: 160 }}>
            <label style={styles.label}>Supports dry-run</label>
            <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
              <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
              <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>Yes</span>
            </label>
          </div>
          <div style={styles.field}>
            <label style={styles.label}>Depends on</label>
            <input
              style={styles.input}
              value={dependsOn}
              onChange={(e) => setDependsOn(e.target.value)}
              placeholder="script-id-1, script-id-2"
            />
          </div>
        </div>
      </div>

      {/* Additional files */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Additional Files (optional)</span>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" as const, alignItems: "center" }}>
          <button
            style={styles.secondaryButton}
            onClick={() => handleUploadAdditional("rollback.sh")}
          >
            {additionalFiles["rollback.sh"] ? "rollback.sh (uploaded)" : "Upload rollback.sh"}
          </button>
          {Object.keys(additionalFiles).filter((f) => f !== "rollback.sh").map((f) => (
            <span key={f} style={{ fontSize: "0.8rem", color: "#6cffa0" }}>{f}</span>
          ))}
          <button
            style={styles.secondaryButton}
            onClick={() => {
              const fname = prompt("Filename:");
              if (fname) handleUploadAdditional(fname);
            }}
          >
            + Add File
          </button>
        </div>
      </div>

      {/* Submit */}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button
          style={styles.primaryButton}
          onClick={() => submit(false)}
          disabled={submitting}
        >
          Register Script
        </button>
        <button
          style={styles.promoteButton}
          onClick={() => submit(true)}
          disabled={submitting}
        >
          Register &amp; Promote to Dev
        </button>
      </div>

      {msg && (
        <div style={{
          fontSize: "0.85rem",
          color: msg.includes("failed") || msg.includes("Error") ? "#ff6c8a" : "#6cffa0",
          marginTop: 8,
        }}>
          {msg}
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 16 },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  card: {
    backgroundColor: "#12121a",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
  fieldRow: { display: "flex", gap: 12, flexWrap: "wrap" as const },
  field: { display: "flex", flexDirection: "column", gap: 4, flex: 1, minWidth: 140 },
  fieldGroup: { display: "flex", flexDirection: "column", gap: 4 },
  label: { fontSize: "0.75rem", color: "#8888a0" },
  input: {
    backgroundColor: "#0a0a12",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: 6,
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
  },
  select: {
    backgroundColor: "#0a0a12",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: 6,
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    maxWidth: 300,
  },
  editor: {
    backgroundColor: "#0a0a12",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: 6,
    padding: 12,
    color: "#e0e0e8",
    fontFamily: "monospace",
    fontSize: "0.85rem",
    resize: "vertical" as const,
    lineHeight: 1.5,
  },
  editorFooter: { display: "flex", justifyContent: "space-between", alignItems: "center" },
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: 8,
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  primaryButton: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: 8,
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
  promoteButton: {
    backgroundColor: "#2a4a2a",
    color: "#6cffa0",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a5a3a",
    borderRadius: 8,
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
  },
};
