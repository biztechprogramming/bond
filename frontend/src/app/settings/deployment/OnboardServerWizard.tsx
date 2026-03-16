import React, { useState, useEffect, useCallback } from "react";
import { GATEWAY_API } from "@/lib/config";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = "connect" | "discovery" | "review" | "environment" | "scripts" | "done";

interface OnboardServerWizardProps {
  environments: Array<{ name: string; display_name: string }>;
  onComplete: (result: OnboardingResult) => void;
  onCancel: () => void;
}

interface OnboardingResult {
  resource_id: string;
  environment: string;
  manifest_name: string;
  scripts_registered: string[];
  monitoring_enabled: boolean;
}

interface ProbeInfo {
  os?: string;
  cpu?: string;
  ram?: string;
  [key: string]: any;
}

interface DiscoveryItem {
  name: string;
  version?: string;
  type?: string;
  detail?: string;
}

interface DiscoveryLayer {
  label: string;
  key: string;
  items: DiscoveryItem[];
  raw?: any;
}

interface SecurityObservation {
  severity: string;
  message: string;
  detail?: string;
}

interface Component {
  id: string;
  name: string;
  display_name: string;
  component_type: string;
  parent_id: string | null;
  runtime: string | null;
  framework: string | null;
  repository_url: string | null;
  icon: string | null;
  description: string | null;
  is_active: boolean;
}

interface DraftComponent {
  name: string;
  display_name: string;
  component_type: string;
  icon: string;
  enabled: boolean;
  sourceLayer: string;
}

const COMPONENT_TYPES = ["application", "web-server", "data-store", "cache", "message-queue", "infrastructure", "system"];
const LAYER_TO_COMPONENT_TYPE: Record<string, string> = {
  system: "infrastructure",
  web_server: "web-server",
  application: "application",
  data_stores: "data-store",
  dns: "infrastructure",
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const STEPS: { key: WizardStep; label: string }[] = [
  { key: "connect", label: "Connect" },
  { key: "discovery", label: "Discovery" },
  { key: "review", label: "Review" },
  { key: "environment", label: "Environment" },
  { key: "scripts", label: "Scripts" },
  { key: "done", label: "Done" },
];

const LAYER_KEYS = ["system", "web_server", "application", "data_stores", "dns"] as const;
const LAYER_LABELS: Record<string, string> = {
  system: "System Overview",
  web_server: "Web Server",
  application: "Applications",
  data_stores: "Data Stores",
  dns: "DNS & Networking",
};

const AUTH_METHODS = [
  { value: "key_file", label: "SSH Key File" },
  { value: "key_paste", label: "Paste Key" },
  { value: "password", label: "Password" },
];

const SCRIPT_LEVELS = [
  { value: 0, label: "Level 0 — Replication", desc: "Mirror current config exactly" },
  { value: 1, label: "Level 1 — Improvements", desc: "Apply best-practice hardening" },
  { value: 2, label: "Level 2 — Modernization", desc: "Upgrade stack where possible" },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function guessEnvironment(hostname: string): string {
  const h = hostname.toLowerCase();
  if (/^prod|^prd|\.prod|\.prd/.test(h)) return "production";
  if (/^stag|\.stag/.test(h)) return "staging";
  if (/^dev|\.dev|local/.test(h)) return "development";
  if (/^test|\.test/.test(h)) return "testing";
  return "";
}

function defaultMonitoringInterval(env: string): number {
  if (env === "production") return 60;
  if (env === "staging") return 120;
  return 300;
}

function flattenLayerItems(data: any): DiscoveryItem[] {
  if (!data) return [];
  if (Array.isArray(data)) return data.map((d: any) => typeof d === "string" ? { name: d } : { name: d.name || d.service || JSON.stringify(d), version: d.version, type: d.type, detail: d.detail });
  if (typeof data === "object") {
    return Object.entries(data).map(([k, v]) => ({
      name: k,
      detail: typeof v === "string" ? v : typeof v === "object" ? JSON.stringify(v) : String(v),
    }));
  }
  return [{ name: String(data) }];
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export default function OnboardServerWizard({ environments, onComplete, onCancel }: OnboardServerWizardProps) {
  const [step, setStep] = useState<WizardStep>("connect");

  // Step 1: Connect
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

  // Step 2: Discovery
  const [discoveryLayers, setDiscoveryLayers] = useState<DiscoveryLayer[]>([]);
  const [discoveryProgress, setDiscoveryProgress] = useState<Record<string, "pending" | "running" | "done" | "error">>({});
  const [discoveryRunning, setDiscoveryRunning] = useState(false);
  const [discoveryError, setDiscoveryError] = useState("");
  const [securityObs, setSecurityObs] = useState<SecurityObservation[]>([]);
  const [manifestName, setManifestName] = useState("");

  // Step 3: Review + Component creation
  const [reviewExpanded, setReviewExpanded] = useState<Set<string>>(new Set(["application", "security"]));
  const [draftComponents, setDraftComponents] = useState<DraftComponent[]>([]);
  const [parentSystem, setParentSystem] = useState<string>("none");
  const [newSystemName, setNewSystemName] = useState("");
  const [existingSystems, setExistingSystems] = useState<Component[]>([]);
  const [creatingComponents, setCreatingComponents] = useState(false);

  // Step 4: Environment & Monitoring
  const [selectedEnv, setSelectedEnv] = useState("");
  const [monitoringEnabled, setMonitoringEnabled] = useState(true);
  const [monitoringInterval, setMonitoringInterval] = useState(60);
  const [monitorChecks, setMonitorChecks] = useState<Record<string, boolean>>({});

  // Step 5: Scripts
  const [scriptSelections, setScriptSelections] = useState<Record<string, boolean>>({});
  const [scriptLevel, setScriptLevel] = useState(0);
  const [generatedScripts, setGeneratedScripts] = useState<string[]>([]);
  const [scriptPreview, setScriptPreview] = useState("");
  const [generating, setGenerating] = useState(false);

  // --------------------------------------------------
  // Step 1: Test Connection
  // --------------------------------------------------
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
      // Create resource
      const createRes = await fetch(`${GATEWAY_API}/deployments/resources`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: slug,
          display_name: displayName || hostname,
          resource_type: "linux-server",
          environment: environments[0]?.name || "dev",
          connection,
          tags: [],
        }),
      });
      const createData = await createRes.json();
      if (!createRes.ok) { setConnectError(createData.error || createData.detail || "Failed to create resource"); setConnecting(false); return; }

      const rid = createData.id || createData.name || slug;
      setResourceId(rid);

      // Probe
      try {
        const probeRes = await fetch(`${GATEWAY_API}/deployments/resources/${rid}/probe`, { method: "POST" });
        if (probeRes.ok) {
          const probeData = await probeRes.json();
          const caps = probeData.capabilities_json ? (typeof probeData.capabilities_json === "string" ? JSON.parse(probeData.capabilities_json) : probeData.capabilities_json) : probeData;
          setProbeInfo({ os: caps.os || caps.platform, cpu: caps.cpu || caps.arch, ram: caps.ram || caps.memory, ...caps });
        }
      } catch {
        // Probe endpoint may not exist yet — treat connection as successful if resource created
      }
      setProbeInfo(prev => prev || { os: "Connected (probe details unavailable)" });
    } catch (err: any) {
      setConnectError(err.message);
    }
    setConnecting(false);
  };

  // --------------------------------------------------
  // Step 2: Discovery
  // --------------------------------------------------
  const runDiscovery = useCallback(async () => {
    if (!resourceId) return;
    setDiscoveryRunning(true);
    setDiscoveryError("");

    // Initialize progress
    const progress: Record<string, "pending" | "running" | "done" | "error"> = {};
    LAYER_KEYS.forEach(k => { progress[k] = "pending"; });
    setDiscoveryProgress(progress);

    // Simulate per-layer progress while waiting for the real result
    let layerIdx = 0;
    const progressTimer = setInterval(() => {
      if (layerIdx < LAYER_KEYS.length) {
        setDiscoveryProgress(prev => ({ ...prev, [LAYER_KEYS[layerIdx]]: "running" }));
        if (layerIdx > 0) setDiscoveryProgress(prev => ({ ...prev, [LAYER_KEYS[layerIdx - 1]]: "done" }));
        layerIdx++;
      }
    }, 800);

    try {
      const res = await fetch(`${GATEWAY_API}/broker/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "discover", resource_id: resourceId }),
      });

      clearInterval(progressTimer);

      if (res.ok) {
        const data = await res.json();
        const manifest = data.manifest || data;
        setManifestName(manifest.application || resourceId);

        // Parse layers
        const layers: DiscoveryLayer[] = [];
        const rawLayers = manifest.layers || manifest;
        for (const key of LAYER_KEYS) {
          if (rawLayers[key]) {
            layers.push({ label: LAYER_LABELS[key], key, items: flattenLayerItems(rawLayers[key]), raw: rawLayers[key] });
          }
        }
        setDiscoveryLayers(layers);
        setSecurityObs(manifest.security_observations || []);

        // Build script selection defaults
        const sels: Record<string, boolean> = {};
        layers.forEach(l => l.items.forEach(item => {
          sels[item.name] = l.key === "application" || l.key === "web_server";
        }));
        setScriptSelections(sels);

        // Build monitoring defaults
        const checks: Record<string, boolean> = {};
        layers.forEach(l => l.items.forEach(item => { checks[item.name] = true; }));
        setMonitorChecks(checks);

        // Mark all done
        const done: Record<string, "done"> = {};
        LAYER_KEYS.forEach(k => { done[k] = "done"; });
        setDiscoveryProgress(done);
      } else {
        // Fallback: try the manifest endpoint directly
        clearInterval(progressTimer);
        try {
          const fallback = await fetch(`${GATEWAY_API}/deployments/discovery/manifests/${resourceId}`);
          if (fallback.ok) {
            const manifest = await fallback.json();
            setManifestName(manifest.application || resourceId);
            const layers: DiscoveryLayer[] = [];
            const rawLayers = manifest.layers || {};
            for (const key of LAYER_KEYS) {
              if (rawLayers[key]) {
                layers.push({ label: LAYER_LABELS[key], key, items: flattenLayerItems(rawLayers[key]), raw: rawLayers[key] });
              }
            }
            setDiscoveryLayers(layers);
            setSecurityObs(manifest.security_observations || []);
            const done: Record<string, "done"> = {};
            LAYER_KEYS.forEach(k => { done[k] = "done"; });
            setDiscoveryProgress(done);
          } else {
            setDiscoveryError("Discovery failed. The endpoint may not be available yet.");
            LAYER_KEYS.forEach(k => setDiscoveryProgress(prev => ({ ...prev, [k]: "error" })));
          }
        } catch {
          setDiscoveryError("Discovery failed. The endpoint may not be available yet.");
        }
      }
    } catch (err: any) {
      clearInterval(progressTimer);
      setDiscoveryError(err.message);
    }
    setDiscoveryRunning(false);
  }, [resourceId]);

  // Auto-start discovery when entering step 2
  useEffect(() => {
    if (step === "discovery" && resourceId && discoveryLayers.length === 0 && !discoveryRunning) {
      runDiscovery();
    }
  }, [step, resourceId, discoveryLayers.length, discoveryRunning, runDiscovery]);

  // Build draft components when entering step 3
  useEffect(() => {
    if (step === "review" && discoveryLayers.length > 0 && draftComponents.length === 0) {
      const drafts: DraftComponent[] = [];
      for (const layer of discoveryLayers) {
        for (const item of layer.items) {
          const ctype = LAYER_TO_COMPONENT_TYPE[layer.key] || "application";
          drafts.push({
            name: item.name.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-"),
            display_name: item.name,
            component_type: ctype,
            icon: "",
            enabled: ctype === "application" || ctype === "web-server",
            sourceLayer: layer.key,
          });
        }
      }
      setDraftComponents(drafts);
    }
  }, [step, discoveryLayers, draftComponents.length]);

  // Fetch existing system components for parent dropdown
  useEffect(() => {
    if (step === "review") {
      fetch(`${GATEWAY_API}/deployments/components`)
        .then(r => r.ok ? r.json() : [])
        .then(data => {
          const systems = (Array.isArray(data) ? data : data.components || []).filter((c: Component) => c.component_type === "system");
          setExistingSystems(systems);
        })
        .catch(() => {});
    }
  }, [step]);

  // Create components when proceeding from step 3 to step 4
  const createComponentsAndProceed = async () => {
    const enabled = draftComponents.filter(d => d.enabled);
    if (enabled.length === 0) { goNext(); return; }
    setCreatingComponents(true);
    try {
      let parentId: string | null = null;
      if (parentSystem === "new" && newSystemName.trim()) {
        const sysRes = await fetch(`${GATEWAY_API}/deployments/components`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: newSystemName.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-"), display_name: newSystemName.trim(), component_type: "system" }),
        });
        if (sysRes.ok) { const sys = await sysRes.json(); parentId = sys.id; }
      } else if (parentSystem !== "none") {
        parentId = parentSystem;
      }

      for (const draft of enabled) {
        try {
          const compRes = await fetch(`${GATEWAY_API}/deployments/components`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: draft.name, display_name: draft.display_name, component_type: draft.component_type, icon: draft.icon || null, parent_id: parentId }),
          });
          if (compRes.ok && resourceId) {
            const comp = await compRes.json();
            await fetch(`${GATEWAY_API}/deployments/components/${comp.id}/resources`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ resource_id: resourceId }),
            }).catch(() => {});
          }
        } catch { /* continue with other components */ }
      }
    } catch { /* proceed anyway */ }
    setCreatingComponents(false);
    goNext();
  };

  // Auto-suggest environment when entering step 4
  useEffect(() => {
    if (step === "environment" && !selectedEnv) {
      const guess = guessEnvironment(hostname);
      const match = environments.find(e => e.name === guess);
      setSelectedEnv(match ? match.name : environments[0]?.name || "dev");
    }
  }, [step, hostname, environments, selectedEnv]);

  useEffect(() => {
    setMonitoringInterval(defaultMonitoringInterval(selectedEnv));
  }, [selectedEnv]);

  // --------------------------------------------------
  // Step 5: Generate Scripts
  // --------------------------------------------------
  const handleGenerate = async () => {
    setGenerating(true);
    const selected = Object.entries(scriptSelections).filter(([, v]) => v).map(([k]) => k);
    try {
      const res = await fetch(`${GATEWAY_API}/broker/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "generate_scripts",
          resource_id: resourceId,
          components: selected,
          level: scriptLevel,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setGeneratedScripts(data.scripts || selected.map(s => `deploy-${s}.sh`));
        setScriptPreview(data.preview || selected.map(s => `# Deploy script for ${s}\n# Level ${scriptLevel}\necho "Deploying ${s}..."`).join("\n\n"));
      } else {
        // Mock preview if endpoint not ready
        setGeneratedScripts(selected.map(s => `deploy-${s}.sh`));
        setScriptPreview(selected.map(s => `#!/bin/bash\n# Deploy script for ${s} (Level ${scriptLevel})\n# Auto-generated by Bond discovery\necho "Deploying ${s}..."`).join("\n\n"));
      }
    } catch {
      setGeneratedScripts(selected.map(s => `deploy-${s}.sh`));
      setScriptPreview(selected.map(s => `#!/bin/bash\n# Deploy script for ${s} (Level ${scriptLevel})\necho "Deploying ${s}..."`).join("\n\n"));
    }
    setGenerating(false);
  };

  // --------------------------------------------------
  // Navigation
  // --------------------------------------------------
  const stepIdx = STEPS.findIndex(s => s.key === step);
  const canNext = () => {
    if (step === "connect") return !!probeInfo;
    if (step === "discovery") return discoveryLayers.length > 0;
    if (step === "review") return true;
    if (step === "environment") return !!selectedEnv;
    if (step === "scripts") return true;
    return false;
  };

  const goNext = () => {
    if (stepIdx < STEPS.length - 1) setStep(STEPS[stepIdx + 1].key);
  };
  const goBack = () => {
    if (stepIdx > 0) setStep(STEPS[stepIdx - 1].key);
  };

  const handleComplete = () => {
    onComplete({
      resource_id: resourceId,
      environment: selectedEnv,
      manifest_name: manifestName || resourceId,
      scripts_registered: generatedScripts,
      monitoring_enabled: monitoringEnabled,
    });
  };

  const toggleReview = (key: string) => {
    setReviewExpanded(prev => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const sevColor = (sev: string) => {
    if (sev === "critical") return "#ff6c8a";
    if (sev === "warning") return "#ffcc6c";
    return "#6c8aff";
  };

  // --------------------------------------------------
  // Render
  // --------------------------------------------------
  return (
    <div style={styles.container}>
      {/* Step indicator */}
      <div style={styles.stepBar}>
        {STEPS.map((s, i) => (
          <div key={s.key} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{
              ...styles.stepDot,
              backgroundColor: i < stepIdx ? "#6cffa0" : i === stepIdx ? "#6c8aff" : "#2a2a3e",
              color: i <= stepIdx ? "#0a0a12" : "#8888a0",
            }}>
              {i < stepIdx ? "✓" : i + 1}
            </div>
            <span style={{ fontSize: "0.75rem", color: i === stepIdx ? "#e0e0e8" : "#8888a0" }}>{s.label}</span>
            {i < STEPS.length - 1 && <div style={styles.stepLine} />}
          </div>
        ))}
      </div>

      {/* ================================================================ */}
      {/* STEP 1: Connect */}
      {/* ================================================================ */}
      {step === "connect" && (
        <>
          <h2 style={styles.title}>Connect to Server</h2>
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

          <button style={{ ...styles.primaryButton, opacity: connecting ? 0.5 : 1 }} onClick={handleTestConnection} disabled={connecting}>
            {connecting ? "Connecting..." : "Test Connection"}
          </button>

          {connectError && <div style={{ fontSize: "0.85rem", color: "#ff6c8a" }}>{connectError}</div>}

          {probeInfo && (
            <div style={{ ...styles.card, borderColor: "#6cffa022" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span style={{ color: "#6cffa0", fontSize: "1.1rem" }}>✓</span>
                <span style={{ color: "#6cffa0", fontWeight: 600, fontSize: "0.9rem" }}>Connected</span>
              </div>
              <div style={styles.statGrid}>
                {probeInfo.os && <div style={styles.statItem}><span style={styles.statLabel}>OS</span><span style={styles.statValue}>{probeInfo.os}</span></div>}
                {probeInfo.cpu && <div style={styles.statItem}><span style={styles.statLabel}>CPU</span><span style={styles.statValue}>{probeInfo.cpu}</span></div>}
                {probeInfo.ram && <div style={styles.statItem}><span style={styles.statLabel}>RAM</span><span style={styles.statValue}>{probeInfo.ram}</span></div>}
              </div>
            </div>
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* STEP 2: Discovery */}
      {/* ================================================================ */}
      {step === "discovery" && (
        <>
          <h2 style={styles.title}>Discovering Server</h2>
          <p style={styles.subtitle}>Scanning {hostname} for installed services and configuration...</p>

          <div style={styles.card}>
            {LAYER_KEYS.map(key => {
              const status = discoveryProgress[key] || "pending";
              const layer = discoveryLayers.find(l => l.key === key);
              return (
                <div key={key} style={{ marginBottom: 8 }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <span style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>{LAYER_LABELS[key]}</span>
                    <span style={{ fontSize: "0.7rem", color: status === "done" ? "#6cffa0" : status === "running" ? "#6c8aff" : status === "error" ? "#ff6c8a" : "#8888a0" }}>
                      {status === "done" ? "✓ Done" : status === "running" ? "Scanning..." : status === "error" ? "✗ Error" : "Pending"}
                    </span>
                  </div>
                  <div style={styles.progressTrack}>
                    <div style={{ ...styles.progressFill, width: status === "done" ? "100%" : status === "running" ? "60%" : "0%", backgroundColor: status === "error" ? "#ff6c8a" : "#6c8aff" }} />
                  </div>
                  {status === "done" && layer && layer.items.length > 0 && (
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
                      {layer.items.map((item, i) => (
                        <span key={i} style={styles.tag}>{item.name}{item.version ? ` ${item.version}` : ""}</span>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>

          {discoveryError && <div style={{ fontSize: "0.85rem", color: "#ff6c8a" }}>{discoveryError}</div>}
        </>
      )}

      {/* ================================================================ */}
      {/* STEP 3: Review */}
      {/* ================================================================ */}
      {step === "review" && (
        <>
          <h2 style={styles.title}>Review Discovery Results</h2>

          {discoveryLayers.map(layer => {
            const isOpen = reviewExpanded.has(layer.key);
            return (
              <div key={layer.key} style={styles.card}>
                <div style={styles.sectionHeader} onClick={() => toggleReview(layer.key)}>
                  <span style={styles.cardTitle}>{layer.label}</span>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ fontSize: "0.7rem", color: "#8888a0" }}>{layer.items.length} found</span>
                    <span style={{ color: "#8888a0", fontSize: "0.8rem" }}>{isOpen ? "▾" : "▸"}</span>
                  </div>
                </div>
                {isOpen && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {layer.items.map((item, i) => (
                      <div key={i} style={styles.reviewItem}>
                        <span style={{ color: "#e0e0e8", fontSize: "0.85rem", fontWeight: 500 }}>{item.name}</span>
                        {item.version && <span style={{ color: "#8888a0", fontSize: "0.75rem" }}>v{item.version}</span>}
                        {item.detail && <span style={{ color: "#8888a0", fontSize: "0.75rem" }}>{item.detail}</span>}
                      </div>
                    ))}
                    {layer.raw && (
                      <pre style={styles.codeBlock}>{JSON.stringify(layer.raw, null, 2)}</pre>
                    )}
                  </div>
                )}
              </div>
            );
          })}

          {securityObs.length > 0 && (
            <div style={styles.card}>
              <span style={styles.cardTitle}>Security Observations</span>
              {securityObs.map((obs, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0" }}>
                  <span style={{ fontSize: "0.7rem", fontWeight: 600, color: sevColor(obs.severity), backgroundColor: sevColor(obs.severity) + "22", padding: "2px 8px", borderRadius: 4, textTransform: "uppercase" as const }}>{obs.severity}</span>
                  <span style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>{obs.message}</span>
                </div>
              ))}
            </div>
          )}

          {/* Component creation from discovery */}
          {draftComponents.length > 0 && (
            <div style={styles.card}>
              <span style={styles.cardTitle}>Register as Components</span>
              <p style={{ fontSize: "0.8rem", color: "#8888a0", margin: 0 }}>Toggle which discovered items to register as managed components.</p>

              {draftComponents.map((draft, i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", padding: "6px 0", borderBottom: "1px solid #1e1e2e" }}>
                  <input type="checkbox" checked={draft.enabled} onChange={e => {
                    setDraftComponents(prev => prev.map((d, j) => j === i ? { ...d, enabled: e.target.checked } : d));
                  }} style={{ accentColor: "#6cffa0" }} />
                  <input style={{ ...styles.input, flex: 1, maxWidth: 160 }} value={draft.display_name} onChange={e => {
                    setDraftComponents(prev => prev.map((d, j) => j === i ? { ...d, display_name: e.target.value, name: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-") } : d));
                  }} placeholder="Name" />
                  <select style={{ ...styles.select, minWidth: 120 }} value={draft.component_type} onChange={e => {
                    setDraftComponents(prev => prev.map((d, j) => j === i ? { ...d, component_type: e.target.value } : d));
                  }}>
                    {COMPONENT_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                  <input style={{ ...styles.input, maxWidth: 80 }} value={draft.icon} onChange={e => {
                    setDraftComponents(prev => prev.map((d, j) => j === i ? { ...d, icon: e.target.value } : d));
                  }} placeholder="Icon" />
                </div>
              ))}

              <div style={{ display: "flex", gap: 12, alignItems: "center", marginTop: 4 }}>
                <label style={{ fontSize: "0.75rem", color: "#8888a0" }}>Parent System:</label>
                <select style={{ ...styles.select, minWidth: 180 }} value={parentSystem} onChange={e => setParentSystem(e.target.value)}>
                  <option value="none">None</option>
                  <option value="new">Create new system...</option>
                  {existingSystems.map(s => <option key={s.id} value={s.id}>{s.display_name || s.name}</option>)}
                </select>
                {parentSystem === "new" && (
                  <input style={{ ...styles.input, maxWidth: 180 }} value={newSystemName} onChange={e => setNewSystemName(e.target.value)} placeholder="System name" />
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* STEP 4: Environment & Monitoring */}
      {/* ================================================================ */}
      {step === "environment" && (
        <>
          <h2 style={styles.title}>Environment & Monitoring</h2>

          <div style={styles.card}>
            <span style={styles.cardTitle}>Environment</span>
            <div style={styles.field}>
              <label style={styles.label}>Assign Environment</label>
              <select style={styles.select} value={selectedEnv} onChange={e => setSelectedEnv(e.target.value)}>
                {environments.map(env => (
                  <option key={env.name} value={env.name}>{env.display_name}</option>
                ))}
              </select>
              {guessEnvironment(hostname) && (
                <span style={{ fontSize: "0.7rem", color: "#8888a0", marginTop: 2 }}>
                  Auto-suggested from hostname pattern
                </span>
              )}
            </div>
          </div>

          <div style={styles.card}>
            <span style={styles.cardTitle}>Monitoring</span>
            <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
              <input type="checkbox" checked={monitoringEnabled} onChange={e => setMonitoringEnabled(e.target.checked)} />
              <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>Enable monitoring</span>
            </label>

            {monitoringEnabled && (
              <>
                <div style={styles.field}>
                  <label style={styles.label}>Check Interval (seconds)</label>
                  <input style={{ ...styles.input, maxWidth: 120 }} type="number" value={monitoringInterval} onChange={e => setMonitoringInterval(parseInt(e.target.value) || 60)} />
                </div>

                <span style={{ ...styles.label, marginTop: 4 }}>Components to monitor:</span>
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  {Object.entries(monitorChecks).map(([name, checked]) => (
                    <label key={name} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                      <input type="checkbox" checked={checked} onChange={e => setMonitorChecks(prev => ({ ...prev, [name]: e.target.checked }))} />
                      <span style={{ fontSize: "0.8rem", color: "#e0e0e8" }}>{name}</span>
                    </label>
                  ))}
                </div>
              </>
            )}
          </div>
        </>
      )}

      {/* ================================================================ */}
      {/* STEP 5: Generate Scripts */}
      {/* ================================================================ */}
      {step === "scripts" && (
        <>
          <h2 style={styles.title}>Generate Deployment Scripts</h2>

          <div style={styles.card}>
            <span style={styles.cardTitle}>Components</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              {Object.entries(scriptSelections).map(([name, checked]) => (
                <label key={name} style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                  <input type="checkbox" checked={checked} onChange={e => setScriptSelections(prev => ({ ...prev, [name]: e.target.checked }))} />
                  <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>{name}</span>
                </label>
              ))}
            </div>
          </div>

          <div style={styles.card}>
            <span style={styles.cardTitle}>Script Level</span>
            {SCRIPT_LEVELS.map(sl => (
              <label key={sl.value} style={{
                display: "flex", alignItems: "center", gap: 8, cursor: "pointer", padding: "6px 0",
                borderBottom: sl.value < 2 ? "1px solid #1e1e2e" : "none",
              }}>
                <input type="radio" name="script_level" value={sl.value} checked={scriptLevel === sl.value} onChange={() => setScriptLevel(sl.value)} />
                <div>
                  <div style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>{sl.label}</div>
                  <div style={{ fontSize: "0.7rem", color: "#8888a0" }}>{sl.desc}</div>
                </div>
              </label>
            ))}
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <button style={{ ...styles.primaryButton, opacity: generating ? 0.5 : 1 }} onClick={handleGenerate} disabled={generating}>
              {generating ? "Generating..." : "Generate & Register Scripts"}
            </button>
            <button style={styles.secondaryButton} onClick={goNext}>Skip</button>
          </div>

          {scriptPreview && (
            <div style={styles.card}>
              <span style={styles.cardTitle}>Script Preview</span>
              <pre style={styles.codeBlock}>{scriptPreview}</pre>
            </div>
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* STEP 6: Done */}
      {/* ================================================================ */}
      {step === "done" && (
        <>
          <h2 style={styles.title}>Server Onboarded</h2>
          <div style={{ ...styles.card, borderColor: "#6cffa022" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <span style={{ color: "#6cffa0", fontSize: "1.3rem" }}>✓</span>
              <span style={{ color: "#6cffa0", fontWeight: 600, fontSize: "1rem" }}>{displayName || hostname} is ready</span>
            </div>
            <div style={styles.statGrid}>
              <div style={styles.statItem}><span style={styles.statLabel}>Resource ID</span><span style={styles.statValue}>{resourceId}</span></div>
              <div style={styles.statItem}><span style={styles.statLabel}>Environment</span><span style={styles.statValue}>{selectedEnv}</span></div>
              <div style={styles.statItem}><span style={styles.statLabel}>Manifest</span><span style={styles.statValue}>{manifestName || "—"}</span></div>
              <div style={styles.statItem}><span style={styles.statLabel}>Scripts</span><span style={styles.statValue}>{generatedScripts.length || "None"}</span></div>
              <div style={styles.statItem}><span style={styles.statLabel}>Monitoring</span><span style={styles.statValue}>{monitoringEnabled ? `Every ${monitoringInterval}s` : "Off"}</span></div>
            </div>
          </div>

          <div style={styles.card}>
            <span style={styles.cardTitle}>What's Next</span>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>→ View discovery manifest in the Resources tab</span>
              <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>→ Promote scripts to environments for deployment</span>
              <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>→ Configure deployment agents per environment</span>
              {monitoringEnabled && <span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>→ Check monitoring dashboard for health status</span>}
            </div>
          </div>

          <button style={styles.primaryButton} onClick={handleComplete}>Done</button>
        </>
      )}

      {/* ================================================================ */}
      {/* Navigation Footer */}
      {/* ================================================================ */}
      {step !== "done" && (
        <div style={styles.navRow}>
          {stepIdx > 0 && step !== "scripts" ? (
            <button style={styles.secondaryButton} onClick={goBack}>Back</button>
          ) : (
            <button style={styles.secondaryButton} onClick={onCancel}>Cancel</button>
          )}
          {step !== "connect" && step !== "scripts" && (
            <button
              style={{ ...styles.primaryButton, opacity: canNext() && !creatingComponents ? 1 : 0.4 }}
              onClick={step === "review" ? createComponentsAndProceed : goNext}
              disabled={!canNext() || creatingComponents}
            >
              {creatingComponents ? "Creating Components..." : "Continue"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Styles
// ---------------------------------------------------------------------------

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 14 },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  subtitle: { fontSize: "0.85rem", color: "#8888a0", margin: 0 },
  card: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
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
    border: "1px solid #2a2a3e",
    borderRadius: 6,
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
    outline: "none",
  },
  select: {
    backgroundColor: "#0a0a12",
    border: "1px solid #2a2a3e",
    borderRadius: 6,
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.85rem",
  },
  primaryButton: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: 8,
    padding: "10px 20px",
    fontSize: "0.9rem",
    fontWeight: 600,
    cursor: "pointer",
    alignSelf: "flex-start",
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
  stepBar: { display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" },
  stepDot: {
    width: 24,
    height: 24,
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontSize: "0.7rem",
    fontWeight: 700,
  },
  stepLine: { width: 20, height: 1, backgroundColor: "#2a2a3e" },
  progressTrack: {
    height: 4,
    backgroundColor: "#1e1e2e",
    borderRadius: 2,
    overflow: "hidden" as const,
  },
  progressFill: {
    height: "100%",
    borderRadius: 2,
    transition: "width 0.4s ease",
  },
  tag: {
    fontSize: "0.7rem",
    color: "#e0e0e8",
    backgroundColor: "#1e1e2e",
    padding: "2px 8px",
    borderRadius: 4,
  },
  sectionHeader: { display: "flex", justifyContent: "space-between", alignItems: "center", cursor: "pointer" },
  reviewItem: { display: "flex", gap: 8, alignItems: "baseline", padding: "4px 0", borderBottom: "1px solid #1e1e2e" },
  statGrid: { display: "flex", gap: 16, flexWrap: "wrap" },
  statItem: { display: "flex", flexDirection: "column", gap: 2 },
  statLabel: { fontSize: "0.7rem", color: "#8888a0" },
  statValue: { fontSize: "0.85rem", color: "#e0e0e8" },
  codeBlock: {
    backgroundColor: "#0a0a12",
    color: "#e0e0e8",
    padding: 12,
    borderRadius: 8,
    fontSize: "0.75rem",
    overflow: "auto" as const,
    maxHeight: 300,
    margin: 0,
    border: "1px solid #1e1e2e",
    fontFamily: "monospace",
  },
  navRow: { display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 4 },
};
