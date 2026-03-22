import React, { useState } from "react";
import { GATEWAY_API } from "@/lib/config";

const RESOURCE_TYPES = [
  { value: "local", label: "Local Machine", icon: "💻" },
  { value: "linux-server", label: "Linux Server", icon: "🖥" },
  { value: "kubernetes", label: "Kubernetes", icon: "☸" },
  { value: "docker-host", label: "Docker Host", icon: "🐳" },
  { value: "aws-ecs", label: "AWS ECS", icon: "☁" },
  { value: "custom", label: "Custom", icon: "⚙" },
];

interface Props {
  environments: { name: string; display_name: string }[];
  onBack: () => void;
  onSaved: () => void;
}

export default function ResourceForm({ environments, onBack, onSaved }: Props) {
  const [resourceType, setResourceType] = useState("local");
  const [name, setName] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [environment, setEnvironment] = useState(environments[0]?.name || "dev");
  const [tags, setTags] = useState("");

  // SSH fields
  const [sshHost, setSshHost] = useState("");
  const [sshUser, setSshUser] = useState("deploy");
  const [sshPort, setSshPort] = useState("22");
  const [sshKeySecret, setSshKeySecret] = useState("");

  // Kubernetes fields
  const [k8sContext, setK8sContext] = useState("");
  const [k8sNamespace, setK8sNamespace] = useState("default");

  const [submitting, setSubmitting] = useState(false);
  const [msg, setMsg] = useState("");
  const [probeResult, setProbeResult] = useState<any>(null);

  const buildConnection = () => {
    switch (resourceType) {
      case "linux-server":
        return { host: sshHost, user: sshUser, port: parseInt(sshPort), key_secret: sshKeySecret };
      case "kubernetes":
        return { context: k8sContext, namespace: k8sNamespace };
      case "local":
        return {};
      default:
        return {};
    }
  };

  const handleTestAndDiscover = async () => {
    if (!name) { setMsg("Name is required."); return; }
    setSubmitting(true);
    setMsg("");
    setProbeResult(null);

    try {
      const res = await fetch(`${GATEWAY_API}/deployments/resources`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          display_name: displayName || name,
          resource_type: resourceType,
          environment,
          connection: buildConnection(),
          tags: tags ? tags.split(",").map(t => t.trim()).filter(Boolean) : [],
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        setMsg(`Error: ${data.error}`);
      } else {
        setProbeResult(data);
        setMsg("Resource created and probed successfully.");
        onSaved();
      }
    } catch (err: any) {
      setMsg(`Error: ${err.message}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={styles.container}>
      <div style={styles.headerRow}>
        <h2 style={styles.title}>Add Deployment Resource</h2>
        <button style={styles.secondaryButton} onClick={onBack}>Cancel</button>
      </div>

      {/* Resource type selector */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Resource Type</span>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" as const }}>
          {RESOURCE_TYPES.map(rt => (
            <label
              key={rt.value}
              style={{
                ...styles.radioCard,
                borderColor: resourceType === rt.value ? "#6c8aff" : "#2a2a3e",
                backgroundColor: resourceType === rt.value ? "#1a1a2e" : "#0a0a12",
              }}
            >
              <input
                type="radio"
                name="resource_type"
                value={rt.value}
                checked={resourceType === rt.value}
                onChange={() => setResourceType(rt.value)}
                style={{ display: "none" }}
              />
              <span style={{ fontSize: "1.2rem" }}>{rt.icon}</span>
              <span style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>{rt.label}</span>
            </label>
          ))}
        </div>
      </div>

      {/* Connection fields */}
      {resourceType === "linux-server" && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>SSH Connection</span>
          <div style={styles.fieldRow}>
            <div style={styles.field}>
              <label style={styles.label}>Host</label>
              <input style={styles.input} value={sshHost} onChange={e => setSshHost(e.target.value)} placeholder="192.168.1.100" />
            </div>
            <div style={{ ...styles.field, maxWidth: 120 }}>
              <label style={styles.label}>Port</label>
              <input style={styles.input} value={sshPort} onChange={e => setSshPort(e.target.value)} />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>User</label>
              <input style={styles.input} value={sshUser} onChange={e => setSshUser(e.target.value)} />
            </div>
          </div>
          <div style={styles.field}>
            <label style={styles.label}>SSH Key Secret Name</label>
            <input style={styles.input} value={sshKeySecret} onChange={e => setSshKeySecret(e.target.value)} placeholder="e.g. SSH_PRIVATE_KEY" />
          </div>
        </div>
      )}

      {resourceType === "kubernetes" && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Kubernetes Connection</span>
          <div style={styles.fieldRow}>
            <div style={styles.field}>
              <label style={styles.label}>Context</label>
              <input style={styles.input} value={k8sContext} onChange={e => setK8sContext(e.target.value)} placeholder="my-cluster" />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>Namespace</label>
              <input style={styles.input} value={k8sNamespace} onChange={e => setK8sNamespace(e.target.value)} />
            </div>
          </div>
        </div>
      )}

      {resourceType === "local" && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Local Machine</span>
          <span style={{ fontSize: "0.85rem", color: "#8888a0" }}>
            No connection configuration needed — will probe this machine directly.
          </span>
        </div>
      )}

      {/* Identity */}
      <div style={styles.card}>
        <span style={styles.cardTitle}>Identity</span>
        <div style={styles.fieldRow}>
          <div style={styles.field}>
            <label style={styles.label}>Name (slug)</label>
            <input
              style={styles.input}
              value={name}
              onChange={e => setName(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""))}
              placeholder="my-server"
            />
          </div>
          <div style={styles.field}>
            <label style={styles.label}>Display Name</label>
            <input style={styles.input} value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="My Server" />
          </div>
        </div>
        <div style={styles.fieldRow}>
          <div style={styles.field}>
            <label style={styles.label}>Environment</label>
            <select style={styles.select} value={environment} onChange={e => setEnvironment(e.target.value)}>
              {environments.map(env => (
                <option key={env.name} value={env.name}>{env.display_name}</option>
              ))}
            </select>
          </div>
          <div style={styles.field}>
            <label style={styles.label}>Tags (comma-separated)</label>
            <input style={styles.input} value={tags} onChange={e => setTags(e.target.value)} placeholder="web, primary" />
          </div>
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
        <button style={styles.primaryButton} onClick={handleTestAndDiscover} disabled={submitting}>
          {submitting ? "Creating..." : "Test Connection & Discover"}
        </button>
      </div>

      {msg && (
        <div style={{ fontSize: "0.85rem", color: msg.includes("Error") ? "#ff6c8a" : "#6cffa0", marginTop: 8 }}>
          {msg}
        </div>
      )}

      {/* Probe results */}
      {probeResult && (
        <div style={styles.card}>
          <span style={styles.cardTitle}>Discovery Results</span>
          {probeResult.capabilities_json && (() => {
            const caps = typeof probeResult.capabilities_json === "string"
              ? JSON.parse(probeResult.capabilities_json) : probeResult.capabilities_json;
            return (
              <div style={{ display: "flex", flexDirection: "column" as const, gap: 4 }}>
                {Object.entries(caps).filter(([, v]) => v && v !== "unknown").map(([k, v]) => (
                  <div key={k} style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>
                    <span style={{ color: "#8888a0" }}>{k}:</span> {String(v)}
                  </div>
                ))}
              </div>
            );
          })()}
          {probeResult.state_json && (() => {
            const state = typeof probeResult.state_json === "string"
              ? JSON.parse(probeResult.state_json) : probeResult.state_json;
            return (
              <div style={{ display: "flex", flexDirection: "column" as const, gap: 4, marginTop: 8 }}>
                <span style={{ fontSize: "0.75rem", color: "#8888a0", textTransform: "uppercase" as const }}>State</span>
                {Object.entries(state).filter(([, v]) => v && v !== "unknown").map(([k, v]) => (
                  <div key={k} style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>
                    <span style={{ color: "#8888a0" }}>{k}:</span> {String(v)}
                  </div>
                ))}
              </div>
            );
          })()}
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
  },
  radioCard: {
    display: "flex",
    flexDirection: "column" as const,
    alignItems: "center",
    gap: 4,
    padding: "10px 14px",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: 8,
    cursor: "pointer",
    minWidth: 80,
  },
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
};
