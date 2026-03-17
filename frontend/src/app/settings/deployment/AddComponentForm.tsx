import React, { useState, useEffect } from "react";
import { GATEWAY_API } from "@/lib/config";
import { useResources, useComponents, callReducer } from "@/hooks/useSpacetimeDB";

interface AddComponentFormProps {
  onComplete: () => void;
  onCancel: () => void;
}

interface ExistingComponent {
  id: string;
  display_name: string;
  component_type: string;
}

interface ExistingResource {
  id: string;
  name: string;
  display_name: string;
}

const COMPONENT_TYPES = [
  "application",
  "web-server",
  "data-store",
  "cache",
  "message-queue",
  "infrastructure",
  "system",
];

function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
}

export default function AddComponentForm({ onComplete, onCancel }: AddComponentFormProps) {
  const [displayName, setDisplayName] = useState("");
  const [componentType, setComponentType] = useState("application");
  const [description, setDescription] = useState("");
  const [runtime, setRuntime] = useState("");
  const [framework, setFramework] = useState("");
  const [repositoryUrl, setRepositoryUrl] = useState("");
  const [icon, setIcon] = useState("");
  const [parentId, setParentId] = useState<string>("");
  const [newSystemName, setNewSystemName] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [port, setPort] = useState("");
  const [healthCheck, setHealthCheck] = useState("");

  const [systems, setSystems] = useState<ExistingComponent[]>([]);
  const [resources, setResources] = useState<ExistingResource[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    fetch(`${GATEWAY_API}/deployments/components`)
      .then((r) => r.ok ? r.json() : [])
      .then((data: ExistingComponent[]) => {
        setSystems(Array.isArray(data) ? data.filter((c) => c.component_type === "system") : []);
      })
      .catch(() => {});

    fetch(`${GATEWAY_API}/deployments/resources`)
      .then((r) => r.ok ? r.json() : [])
      .then((data: ExistingResource[]) => {
        setResources(Array.isArray(data) ? data : []);
      })
      .catch(() => {});
  }, []);

  const slug = slugify(displayName);

  const handleSubmit = async () => {
    if (!displayName.trim() || !componentType) return;
    setSaving(true);
    setError("");

    try {
      let finalParentId = parentId;

      // Create new system if requested
      if (parentId === "__new__" && newSystemName.trim()) {
        const sysRes = await fetch(`${GATEWAY_API}/deployments/components`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: slugify(newSystemName),
            display_name: newSystemName.trim(),
            component_type: "system",
          }),
        });
        if (!sysRes.ok) throw new Error("Failed to create parent system");
        const sysData = await sysRes.json();
        finalParentId = sysData.id;
      }

      const body: Record<string, any> = {
        name: slug,
        display_name: displayName.trim(),
        component_type: componentType,
        ...(finalParentId && finalParentId !== "__new__" && { parent_id: finalParentId }),
        ...(runtime && { runtime }),
        ...(framework && { framework }),
        ...(repositoryUrl && { repository_url: repositoryUrl }),
        ...(icon && { icon }),
        ...(description && { description }),
      };

      const res = await fetch(`${GATEWAY_API}/deployments/components`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `HTTP ${res.status}`);
      }
      const created = await res.json();

      // Link to server if selected
      if (resourceId) {
        const linkBody: Record<string, any> = { resource_id: resourceId };
        if (port) linkBody.port = Number(port);
        if (healthCheck) linkBody.health_check = healthCheck;

        await fetch(`${GATEWAY_API}/deployments/components/${created.id}/resources`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(linkBody),
        });
      }

      onComplete();
    } catch (e: any) {
      setError(e.message || "Failed to create component");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div style={styles.container}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h2 style={styles.title}>Add Component</h2>
        <button style={styles.secondaryButton} onClick={onCancel}>Cancel</button>
      </div>

      <div style={styles.card}>
        <div style={styles.fieldGroup}>
          <label style={styles.label}>Display Name *</label>
          <input
            style={styles.input}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
            placeholder="My Service"
          />
          {slug && <div style={{ fontSize: "0.7rem", color: "#5a5a6e", marginTop: 2 }}>slug: {slug}</div>}
        </div>

        <div style={styles.fieldGroup}>
          <label style={styles.label}>Component Type *</label>
          <select style={styles.input} value={componentType} onChange={(e) => setComponentType(e.target.value)}>
            {COMPONENT_TYPES.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        </div>

        <div style={styles.fieldGroup}>
          <label style={styles.label}>Description</label>
          <textarea
            style={{ ...styles.input, minHeight: 60, resize: "vertical" }}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Optional description"
          />
        </div>
      </div>

      <div style={styles.card}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Runtime</label>
            <input style={styles.input} value={runtime} onChange={(e) => setRuntime(e.target.value)} placeholder="e.g. node, python, go" />
          </div>
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Framework</label>
            <input style={styles.input} value={framework} onChange={(e) => setFramework(e.target.value)} placeholder="e.g. express, django, gin" />
          </div>
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Repository URL</label>
            <input style={styles.input} value={repositoryUrl} onChange={(e) => setRepositoryUrl(e.target.value)} placeholder="https://github.com/..." />
          </div>
          <div style={styles.fieldGroup}>
            <label style={styles.label}>Icon</label>
            <input style={styles.input} value={icon} onChange={(e) => setIcon(e.target.value)} placeholder="emoji or icon name" />
          </div>
        </div>
      </div>

      <div style={styles.card}>
        <div style={styles.fieldGroup}>
          <label style={styles.label}>Parent System</label>
          <select style={styles.input} value={parentId} onChange={(e) => setParentId(e.target.value)}>
            <option value="">None</option>
            {systems.map((s) => (
              <option key={s.id} value={s.id}>{s.display_name}</option>
            ))}
            <option value="__new__">Create new system...</option>
          </select>
          {parentId === "__new__" && (
            <input
              style={{ ...styles.input, marginTop: 8 }}
              value={newSystemName}
              onChange={(e) => setNewSystemName(e.target.value)}
              placeholder="New system name"
            />
          )}
        </div>

        <div style={styles.fieldGroup}>
          <label style={styles.label}>Link to Server</label>
          <select style={styles.input} value={resourceId} onChange={(e) => setResourceId(e.target.value)}>
            <option value="">None</option>
            {resources.map((r) => (
              <option key={r.id} value={r.id}>{r.display_name || r.name}</option>
            ))}
          </select>
        </div>

        {resourceId && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <div style={styles.fieldGroup}>
              <label style={styles.label}>Port</label>
              <input style={styles.input} type="number" value={port} onChange={(e) => setPort(e.target.value)} placeholder="e.g. 3000" />
            </div>
            <div style={styles.fieldGroup}>
              <label style={styles.label}>Health Check URL</label>
              <input style={styles.input} value={healthCheck} onChange={(e) => setHealthCheck(e.target.value)} placeholder="/health" />
            </div>
          </div>
        )}
      </div>

      {error && <div style={{ fontSize: "0.85rem", color: "#ff6c8a" }}>{error}</div>}

      <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
        <button style={styles.secondaryButton} onClick={onCancel}>Cancel</button>
        <button
          style={{ ...styles.primaryButton, opacity: !displayName.trim() || saving ? 0.5 : 1 }}
          disabled={!displayName.trim() || saving}
          onClick={handleSubmit}
        >
          {saving ? "Creating..." : "Create Component"}
        </button>
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 14 },
  card: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 12,
  },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  label: { fontSize: "0.75rem", color: "#8888a0", marginBottom: 4, display: "block" },
  input: {
    backgroundColor: "#0a0a12",
    border: "1px solid #2a2a3e",
    borderRadius: 6,
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    width: "100%",
    boxSizing: "border-box" as const,
  },
  fieldGroup: { display: "flex", flexDirection: "column" as const },
  primaryButton: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: 8,
    padding: "10px 20px",
    fontSize: "0.85rem",
    cursor: "pointer",
    fontWeight: 600,
  },
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: 8,
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
};
