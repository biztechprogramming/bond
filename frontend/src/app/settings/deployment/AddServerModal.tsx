import React, { useState } from "react";
import { GATEWAY_API } from "@/lib/config";
import { callReducer } from "@/hooks/useSpacetimeDB";

interface AddServerModalProps {
  environments: Array<{ name: string; display_name: string }>;
  onComplete: (result: { resource_id: string; display_name: string }) => void;
  onCancel: () => void;
}

interface ProbeInfo {
  os?: string;
  cpu?: string;
  ram?: string;
  [key: string]: any;
}

const AUTH_METHODS = [
  { value: "key_file", label: "SSH Key File" },
  { value: "key_paste", label: "Paste Key" },
  { value: "password", label: "Password" },
];

export default function AddServerModal({ environments, onComplete, onCancel }: AddServerModalProps) {
  const [hostname, setHostname] = useState("");
  const [sshUser, setSshUser] = useState("deploy");
  const [sshPort, setSshPort] = useState("22");
  const [authMethod, setAuthMethod] = useState("key_file");
  const [keyPath, setKeyPath] = useState("~/.ssh/id_ed25519");
  const [keyPaste, setKeyPaste] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [resourceId, setResourceId] = useState("");
  const [probeInfo, setProbeInfo] = useState<ProbeInfo | null>(null);

  const handleTestConnection = async () => {
    if (!hostname) { setConnectError("Hostname is required."); return; }
    setConnecting(true);
    setConnectError("");
    setProbeInfo(null);

    const slug = (displayName || hostname).toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-");
    const connection: Record<string, any> = { host: hostname, user: sshUser, port: parseInt(sshPort) };
    if (authMethod === "key_file") connection.key_path = keyPath;
    else if (authMethod === "key_paste") connection.key_data = keyPaste;
    else connection.password = password;

    try {
      const rid = crypto.randomUUID().replace(/-/g, "");
      const now = BigInt(Date.now());
      callReducer(conn => conn.reducers.createDeploymentResource({
        id: rid,
        name: slug,
        displayName: displayName || hostname,
        resourceType: "linux-server",
        environment: "",  // no longer required — use resource_environments join table
        connectionJson: JSON.stringify(connection),
        capabilitiesJson: "{}",
        stateJson: "{}",
        tagsJson: "[]",
        recommendationsJson: "[]",
        isActive: true,
        createdAt: now,
        updatedAt: now,
        lastProbedAt: BigInt(0),
      }));
      setResourceId(rid);

      try {
        const probeRes = await fetch(`${GATEWAY_API}/deployments/resources/${rid}/probe`, { method: "POST" });
        if (probeRes.ok) {
          const probeData = await probeRes.json();
          const caps = probeData.capabilities_json ? (typeof probeData.capabilities_json === "string" ? JSON.parse(probeData.capabilities_json) : probeData.capabilities_json) : probeData;
          setProbeInfo({ os: caps.os || caps.platform, cpu: caps.cpu || caps.arch, ram: caps.ram || caps.memory, ...caps });
        }
      } catch {
        // Probe endpoint may not exist yet
      }
      setProbeInfo(prev => prev || { os: "Connected (probe details unavailable)" });
    } catch (err: any) {
      setConnectError(err.message);
    }
    setConnecting(false);
  };

  const handleSave = () => {
    onComplete({ resource_id: resourceId, display_name: displayName || hostname });
  };

  return (
    <div style={styles.overlay} onClick={onCancel}>
      <div style={styles.modal} onClick={e => e.stopPropagation()}>
        <div style={styles.modalHeader}>
          <h2 style={styles.title}>Add Server</h2>
          <button style={styles.closeBtn} onClick={onCancel}>&times;</button>
        </div>

        <div style={styles.card}>
          <span style={styles.cardTitle}>SSH Connection</span>
          <div style={styles.fieldRow}>
            <div style={styles.field}>
              <label style={styles.label}>Hostname / IP</label>
              <input style={styles.input} value={hostname} onChange={e => setHostname(e.target.value)} placeholder="192.168.1.100 or server.example.com" />
            </div>
            <div style={{ ...styles.field, maxWidth: 100 }}>
              <label style={styles.label}>Port</label>
              <input style={styles.input} value={sshPort} onChange={e => setSshPort(e.target.value)} />
            </div>
            <div style={styles.field}>
              <label style={styles.label}>User</label>
              <input style={styles.input} value={sshUser} onChange={e => setSshUser(e.target.value)} />
            </div>
          </div>

          <div style={styles.fieldRow}>
            <div style={styles.field}>
              <label style={styles.label}>Auth Method</label>
              <select style={styles.select} value={authMethod} onChange={e => setAuthMethod(e.target.value)}>
                {AUTH_METHODS.map(m => <option key={m.value} value={m.value}>{m.label}</option>)}
              </select>
            </div>
            {authMethod === "key_file" && (
              <div style={{ ...styles.field, flex: 2 }}>
                <label style={styles.label}>Key Path</label>
                <input style={styles.input} value={keyPath} onChange={e => setKeyPath(e.target.value)} placeholder="~/.ssh/id_ed25519" />
              </div>
            )}
          </div>

          {authMethod === "key_paste" && (
            <div style={styles.field}>
              <label style={styles.label}>Paste Private Key</label>
              <textarea style={{ ...styles.input, minHeight: 80, fontFamily: "monospace", fontSize: "0.75rem" }} value={keyPaste} onChange={e => setKeyPaste(e.target.value)} placeholder="-----BEGIN OPENSSH PRIVATE KEY-----" />
            </div>
          )}

          {authMethod === "password" && (
            <div style={styles.field}>
              <label style={styles.label}>Password</label>
              <input style={styles.input} type="password" value={password} onChange={e => setPassword(e.target.value)} />
            </div>
          )}

          <div style={styles.field}>
            <label style={styles.label}>Display Name (optional)</label>
            <input style={styles.input} value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="My Production Server" />
          </div>
        </div>

        {!probeInfo && (
          <button style={{ ...styles.primaryButton, opacity: connecting ? 0.5 : 1 }} onClick={handleTestConnection} disabled={connecting}>
            {connecting ? "Connecting..." : "Test Connection"}
          </button>
        )}

        {connectError && <div style={{ fontSize: "0.85rem", color: "#ff6c8a" }}>{connectError}</div>}

        {probeInfo && (
          <>
            <div style={{ ...styles.card, borderColor: "#6cffa022" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: "#6cffa0", fontSize: "1.1rem" }}>&#10003;</span>
                <span style={{ color: "#6cffa0", fontWeight: 600, fontSize: "0.9rem" }}>Connected</span>
              </div>
              <div style={styles.statGrid}>
                {probeInfo.os && <div style={styles.statItem}><span style={styles.statLabel}>OS</span><span style={styles.statValue}>{probeInfo.os}</span></div>}
                {probeInfo.cpu && <div style={styles.statItem}><span style={styles.statLabel}>CPU</span><span style={styles.statValue}>{probeInfo.cpu}</span></div>}
                {probeInfo.ram && <div style={styles.statItem}><span style={styles.statLabel}>RAM</span><span style={styles.statValue}>{probeInfo.ram}</span></div>}
              </div>
            </div>
            <button style={styles.primaryButton} onClick={handleSave}>
              Add Server
            </button>
          </>
        )}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  overlay: {
    position: "fixed",
    inset: 0,
    backgroundColor: "rgba(0,0,0,0.6)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    zIndex: 1000,
  },
  modal: {
    backgroundColor: "#1a1a2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a3a4e",
    borderRadius: 16,
    padding: 24,
    width: "100%",
    maxWidth: 600,
    maxHeight: "90vh",
    overflowY: "auto",
    display: "flex",
    flexDirection: "column",
    gap: 14,
  },
  modalHeader: {
    display: "flex",
    justifyContent: "space-between",
    alignItems: "center",
  },
  closeBtn: {
    background: "none",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    color: "#8888a0",
    fontSize: "1.4rem",
    cursor: "pointer",
    padding: "4px 8px",
  },
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
    outline: "none",
  },
  select: {
    backgroundColor: "#0a0a12",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: 6,
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
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
    alignSelf: "flex-start",
  },
  statGrid: { display: "flex", gap: 16, flexWrap: "wrap" },
  statItem: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: "0.7rem", color: "#8888a0" },
  statValue: { fontSize: "0.85rem", color: "#e0e0e8" },
};
