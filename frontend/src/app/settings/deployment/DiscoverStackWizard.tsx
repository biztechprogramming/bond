import React, { useState, useEffect, useCallback, useMemo } from "react";
import { GATEWAY_API } from "@/lib/config";
import { useResources, useComponents, callReducer, useSettingsMap, useAgents, useAgentMounts } from "@/hooks/useSpacetimeDB";
import AddServerModal from "./AddServerModal";
import AgentDiscoveryView from "@/components/discovery/AgentDiscoveryView";
import type { ConversationMessage } from "@/hooks/useAgentDiscovery";
import type { DiscoveryState, CompletenessReport } from "@/lib/discovery-types";


// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type WizardStep = "select-agent" | "discovery" | "review" | "environment" | "scripts" | "done";

interface DiscoverStackWizardProps {
  environments: Array<{ name: string; display_name: string }>;
  onComplete: () => void;
  onCancel: () => void;
}

interface ServerResource {
  id: string;
  name: string;
  display_name: string;
  is_active: boolean;
  last_probed_at: string | null;
  status: string;
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

const SCRIPT_LEVELS = [
  { value: 0, label: "Level 0 — Replication", desc: "Mirror current config exactly" },
  { value: 1, label: "Level 1 — Improvements", desc: "Apply best-practice hardening" },
  { value: 2, label: "Level 2 — Modernization", desc: "Upgrade stack where possible" },
];
const LAYER_TO_COMPONENT_TYPE: Record<string, string> = {
  system: "infrastructure",
  web_server: "web-server",
  application: "application",
  data_stores: "data-store",
  dns: "infrastructure",
};

const STEPS: { key: WizardStep; label: string }[] = [
  { key: "select-agent", label: "Select Agent & Repo" },
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  return `${Math.floor(hrs / 24)}d ago`;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const WIZARD_PROGRESS_KEY = "deployment_wizard.progress";

interface WizardProgress {
  step: WizardStep;
  selectedAgentId: string;
  selectedRepoId: string;
  selectedServerId: string;
  selectedRepoUrl: string;
  agentDiscoveryState: DiscoveryState | null;
  selectedEnv: string;
  timestamp: number;
  // Discovery tab
  discoveryLayers: DiscoveryLayer[];
  securityObs: SecurityObservation[];
  manifestName: string;
  conversationMessages: Array<{ id: string; type: string; content: string; toolName?: string; timestamp: number }>;
  // Review tab
  draftComponents: DraftComponent[];
  parentSystem: string;
  newSystemName: string;
  reviewExpanded: string[];
  // Environment tab
  monitoringEnabled: boolean;
  monitoringInterval: number;
  monitorChecks: Record<string, boolean>;
  // Scripts tab
  scriptSelections: Record<string, boolean>;
  scriptLevel: number;
  generatedScripts: string[];
  scriptPreview: string;
}

export default function DiscoverStackWizard({ environments, onComplete, onCancel }: DiscoverStackWizardProps) {
  // --- Issue 2: Restore wizard progress from STDB settings ---
  const settingsMap = useSettingsMap();
  const savedProgress = useMemo<WizardProgress | null>(() => {
    const raw = settingsMap[WIZARD_PROGRESS_KEY];
    if (!raw) return null;
    try { return JSON.parse(raw) as WizardProgress; } catch { return null; }
  }, [settingsMap]);

  const [step, setStep] = useState<WizardStep>(() => savedProgress?.step || "select-agent");

  // Agent selection
  const agents = useAgents();
  const [selectedAgentId, setSelectedAgentId] = useState<string>(() => savedProgress?.selectedAgentId || "");
  const [selectedRepoId, setSelectedRepoId] = useState<string>(() => savedProgress?.selectedRepoId || "");
  const agentMounts = useAgentMounts(selectedAgentId);

  // Auto-select if only one agent
  useEffect(() => {
    if (agents.length === 1 && !selectedAgentId) {
      setSelectedAgentId(agents[0].id);
    }
  }, [agents, selectedAgentId]);

  // Auto-select if only one mount
  useEffect(() => {
    if (agentMounts.length === 1 && !selectedRepoId) {
      setSelectedRepoId(agentMounts[0].mountName || agentMounts[0].hostPath);
    }
  }, [agentMounts, selectedRepoId]);

  // Reset repo when agent changes
  useEffect(() => {
    setSelectedRepoId("");
  }, [selectedAgentId]);

  // Server selection (optional)
  const stdbResources = useResources();
  const servers: ServerResource[] = stdbResources.map(r => {
    const state = (() => { try { return JSON.parse(r.stateJson || "{}"); } catch { return {}; } })();
    return {
      id: r.id || r.name,
      name: r.name,
      display_name: r.displayName || r.name,
      is_active: r.isActive !== false,
      last_probed_at: r.lastProbedAt ? String(r.lastProbedAt) : null,
      status: state.status || (r.isActive ? (r.lastProbedAt ? "online" : "unknown") : "offline"),
    };
  });
  const loadingServers = false; // STDB subscriptions provide data reactively
  const [selectedServerId, setSelectedServerId] = useState<string>(() => savedProgress?.selectedServerId || "");
  const [showAddModal, setShowAddModal] = useState(false);

  // --- Issue 1: Repo selection from known repos ---
  const [selectedRepoUrl, setSelectedRepoUrl] = useState<string>(() => savedProgress?.selectedRepoUrl || "");
  const [showManualRepoInput, setShowManualRepoInput] = useState(false);
  const [manualRepoUrl, setManualRepoUrl] = useState("");
  const stdbComponents = useComponents();
  const knownRepoUrls = useMemo(() => {
    const urls = new Set<string>();
    stdbComponents.forEach(c => {
      if (c.repositoryUrl && c.repositoryUrl.trim()) urls.add(c.repositoryUrl.trim());
    });
    return Array.from(urls).sort();
  }, [stdbComponents]);

  // Step 2: Discovery
  const [discoveryLayers, setDiscoveryLayers] = useState<DiscoveryLayer[]>(() => savedProgress?.discoveryLayers || []);
  const [discoveryProgress, setDiscoveryProgress] = useState<Record<string, "pending" | "running" | "done" | "error">>({});
  const [discoveryRunning, setDiscoveryRunning] = useState(false);
  const [discoveryError, setDiscoveryError] = useState("");
  const [securityObs, setSecurityObs] = useState<SecurityObservation[]>(() => savedProgress?.securityObs || []);
  const [manifestName, setManifestName] = useState(() => savedProgress?.manifestName || "");
  const [conversationMessages, setConversationMessages] = useState<ConversationMessage[]>(() =>
    (savedProgress?.conversationMessages as ConversationMessage[] | undefined) || []
  );

  // Step 3: Review + Component creation
  const [reviewExpanded, setReviewExpanded] = useState<Set<string>>(() =>
    savedProgress?.reviewExpanded ? new Set(savedProgress.reviewExpanded) : new Set(["application", "security"])
  );
  const [draftComponents, setDraftComponents] = useState<DraftComponent[]>(() => savedProgress?.draftComponents || []);
  const [parentSystem, setParentSystem] = useState<string>(() => savedProgress?.parentSystem || "none");
  const [newSystemName, setNewSystemName] = useState(() => savedProgress?.newSystemName || "");
  const existingSystems: Component[] = stdbComponents
    .filter(c => c.componentType === "system")
    .map(c => ({ id: c.id, name: c.name, display_name: c.displayName, component_type: c.componentType, parent_id: c.parentId || null, is_active: c.isActive }));
  const [creatingComponents, setCreatingComponents] = useState(false);

  // Step 4: Environment & Monitoring
  const [selectedEnv, setSelectedEnv] = useState(() => savedProgress?.selectedEnv || "");
  const [monitoringEnabled, setMonitoringEnabled] = useState(() => savedProgress?.monitoringEnabled ?? true);
  const [monitoringInterval, setMonitoringInterval] = useState(() => savedProgress?.monitoringInterval ?? 60);
  const [monitorChecks, setMonitorChecks] = useState<Record<string, boolean>>(() => savedProgress?.monitorChecks || {});

  // Agent discovery fallback flag
  const [agentDiscoveryState, setAgentDiscoveryState] = useState<DiscoveryState | null>(() => savedProgress?.agentDiscoveryState || null);

  // Step 5: Scripts
  const [scriptSelections, setScriptSelections] = useState<Record<string, boolean>>(() => savedProgress?.scriptSelections || {});
  const [scriptLevel, setScriptLevel] = useState(() => savedProgress?.scriptLevel ?? 0);
  const [generatedScripts, setGeneratedScripts] = useState<string[]>(() => savedProgress?.generatedScripts || []);
  const [scriptPreview, setScriptPreview] = useState(() => savedProgress?.scriptPreview || "");
  const [generating, setGenerating] = useState(false);

  const selectedServer = servers.find(s => s.id === selectedServerId);

  // --- Issue 2: Save progress at key milestones ---
  const saveProgress = useCallback((overrides?: Partial<WizardProgress>) => {
    const progress: WizardProgress = {
      step,
      selectedAgentId,
      selectedRepoId,
      selectedServerId,
      selectedRepoUrl,
      agentDiscoveryState,
      selectedEnv,
      timestamp: Date.now(),
      // Discovery tab
      discoveryLayers,
      securityObs,
      manifestName,
      conversationMessages: conversationMessages.map(m => ({
        id: m.id,
        type: m.type,
        content: m.content.slice(0, 2000), // cap message size
        toolName: m.toolName,
        timestamp: m.timestamp,
      })),
      // Review tab
      draftComponents,
      parentSystem,
      newSystemName,
      reviewExpanded: Array.from(reviewExpanded),
      // Environment tab
      monitoringEnabled,
      monitoringInterval,
      monitorChecks,
      // Scripts tab
      scriptSelections,
      scriptLevel,
      generatedScripts,
      scriptPreview: scriptPreview.slice(0, 5000), // cap preview size
      ...overrides,
    };
    callReducer(conn => conn.reducers.setSetting({
      key: WIZARD_PROGRESS_KEY,
      value: JSON.stringify(progress),
      keyType: "string",
    }));
  }, [step, selectedAgentId, selectedRepoId, selectedServerId, selectedRepoUrl, agentDiscoveryState, selectedEnv,
      discoveryLayers, securityObs, manifestName, conversationMessages,
      draftComponents, parentSystem, newSystemName, reviewExpanded,
      monitoringEnabled, monitoringInterval, monitorChecks,
      scriptSelections, scriptLevel, generatedScripts, scriptPreview]);

  const clearProgress = useCallback(() => {
    callReducer(conn => conn.reducers.setSetting({
      key: WIZARD_PROGRESS_KEY,
      value: "",
      keyType: "string",
    }));
  }, []);

  // Auto-save at step transitions
  useEffect(() => {
    if (step !== "select-agent") {
      saveProgress();
    }
  }, [step]); // eslint-disable-line react-hooks/exhaustive-deps

  // Save when discovery completes with findings
  useEffect(() => {
    if (step === "discovery" && agentDiscoveryState && Object.keys(agentDiscoveryState.findings || {}).length > 0) {
      saveProgress();
    }
  }, [agentDiscoveryState]); // eslint-disable-line react-hooks/exhaustive-deps

  // Save when draft components are toggled in review
  useEffect(() => {
    if (step === "review" && draftComponents.length > 0) {
      saveProgress();
    }
  }, [draftComponents]); // eslint-disable-line react-hooks/exhaustive-deps

  // Save when scripts are generated
  useEffect(() => {
    if (step === "scripts" && generatedScripts.length > 0) {
      saveProgress();
    }
  }, [generatedScripts]); // eslint-disable-line react-hooks/exhaustive-deps

  // Discovery is now handled by AgentDiscoveryView in step 2

  // Build draft components when entering step 3 — from agent discovery state or legacy layers
  useEffect(() => {
    if (step === "review" && draftComponents.length === 0) {
      const drafts: DraftComponent[] = [];

      if (agentDiscoveryState && Object.keys(agentDiscoveryState.findings || {}).length > 0) {
        // Map agent discovery findings to draft components
        const findings = agentDiscoveryState.findings as Record<string, any>;
        // Build layers from findings for the review UI
        const layers: DiscoveryLayer[] = [];
        for (const key of LAYER_KEYS) {
          if (findings[key]) {
            layers.push({ label: LAYER_LABELS[key], key, items: flattenLayerItems(findings[key]), raw: findings[key] });
          }
        }
        if (layers.length > 0) setDiscoveryLayers(layers);

        // Also create drafts from any named items in findings
        for (const layer of layers) {
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

        // If no layer-based items, create components from top-level findings
        if (drafts.length === 0) {
          for (const [field, value] of Object.entries(findings)) {
            if (typeof value === "string" && value && !LAYER_KEYS.includes(field as any)) {
              drafts.push({
                name: field.toLowerCase().replace(/[^a-z0-9-]/g, "-").replace(/-+/g, "-"),
                display_name: `${field}: ${value}`,
                component_type: "application",
                icon: "",
                enabled: true,
                sourceLayer: "findings",
              });
            }
          }
        }
      } else if (discoveryLayers.length > 0) {
        // Legacy path: build from discovery layers
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
      }
      if (drafts.length > 0) setDraftComponents(drafts);
    }
  }, [step, discoveryLayers, agentDiscoveryState, draftComponents.length]);

  // existingSystems now derived from useComponents() STDB hook above

  // Create components and proceed to environment step
  const createComponentsAndProceed = async () => {
    const enabled = draftComponents.filter(d => d.enabled);
    if (enabled.length === 0) { goNext(); return; }
    setCreatingComponents(true);
    try {
      let parentId: string | null = null;
      if (parentSystem === "new" && newSystemName.trim()) {
        const sysId = crypto.randomUUID().replace(/-/g, "");
        callReducer(conn => conn.reducers.createComponent({
          id: sysId,
          name: newSystemName.trim().toLowerCase().replace(/[^a-z0-9-]/g, "-"),
          displayName: newSystemName.trim(),
          componentType: "system",
          parentId: "",
          runtime: "",
          framework: "",
          repositoryUrl: "",
          icon: "",
          description: "",
          discoveredFrom: "",
          sourcePath: "",
        }));
        parentId = sysId;
      } else if (parentSystem !== "none") {
        parentId = parentSystem;
      }

      for (const draft of enabled) {
        const compId = crypto.randomUUID().replace(/-/g, "");
        callReducer(conn => conn.reducers.createComponent({
          id: compId,
          name: draft.name,
          displayName: draft.display_name,
          componentType: draft.component_type,
          parentId: parentId || "",
          runtime: "",
          framework: "",
          repositoryUrl: "",
          icon: draft.icon || "",
          description: "",
          discoveredFrom: selectedServerId,
          sourcePath: "",
        }));

        if (selectedServerId) {
          const crId = crypto.randomUUID().replace(/-/g, "");
          callReducer(conn => conn.reducers.addComponentResource({
            id: crId,
            componentId: compId,
            resourceId: selectedServerId,
            environment: "",
            port: 0,
            processName: "",
            healthCheck: "",
          }));
        }
      }
    } catch { /* proceed */ }
    setCreatingComponents(false);
    goNext();
  };

  // Helpers
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

  // Auto-suggest environment when entering step 4
  useEffect(() => {
    if (step === "environment" && !selectedEnv) {
      const serverName = selectedServer?.name || selectedServer?.display_name || "";
      const guess = guessEnvironment(serverName);
      const match = environments.find(e => e.name === guess);
      setSelectedEnv(match ? match.name : environments[0]?.name || "dev");
    }
  }, [step, selectedServer, environments, selectedEnv]);

  useEffect(() => {
    setMonitoringInterval(defaultMonitoringInterval(selectedEnv));
  }, [selectedEnv]);

  // Build script/monitoring defaults from discovery
  useEffect(() => {
    if (step === "environment" && discoveryLayers.length > 0 && Object.keys(monitorChecks).length === 0) {
      const checks: Record<string, boolean> = {};
      discoveryLayers.forEach(l => l.items.forEach(item => { checks[item.name] = true; }));
      setMonitorChecks(checks);
    }
  }, [step, discoveryLayers, monitorChecks]);

  useEffect(() => {
    if (step === "scripts" && discoveryLayers.length > 0 && Object.keys(scriptSelections).length === 0) {
      const sels: Record<string, boolean> = {};
      discoveryLayers.forEach(l => l.items.forEach(item => {
        sels[item.name] = l.key === "application" || l.key === "web_server";
      }));
      setScriptSelections(sels);
    }
  }, [step, discoveryLayers, scriptSelections]);

  // Generate scripts
  const handleGenerate = async () => {
    setGenerating(true);
    const selected = Object.entries(scriptSelections).filter(([, v]) => v).map(([k]) => k);
    try {
      const res = await fetch(`${GATEWAY_API}/broker/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: "generate_scripts",
          resource_id: selectedServerId,
          components: selected,
          level: scriptLevel,
        }),
      });
      if (res.ok) {
        const data = await res.json();
        setGeneratedScripts(data.scripts || selected.map(s => `deploy-${s}.sh`));
        setScriptPreview(data.preview || selected.map(s => `# Deploy script for ${s}\n# Level ${scriptLevel}\necho "Deploying ${s}..."`).join("\n\n"));
      } else {
        setGeneratedScripts(selected.map(s => `deploy-${s}.sh`));
        setScriptPreview(selected.map(s => `#!/bin/bash\n# Deploy script for ${s} (Level ${scriptLevel})\n# Auto-generated by discovery\necho "Deploying ${s}..."`).join("\n\n"));
      }
    } catch {
      setGeneratedScripts(selected.map(s => `deploy-${s}.sh`));
      setScriptPreview(selected.map(s => `#!/bin/bash\n# Deploy script for ${s} (Level ${scriptLevel})\necho "Deploying ${s}..."`).join("\n\n"));
    }
    setGenerating(false);
  };

  // Navigation
  const stepIdx = STEPS.findIndex(s => s.key === step);
  const canNext = () => {
    if (step === "select-agent") return selectedAgentId.length > 0 && (selectedRepoId.length > 0 || agentMounts.length === 0);
    if (step === "discovery") return !!agentDiscoveryState || discoveryLayers.length > 0;
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
              {i < stepIdx ? "\u2713" : i + 1}
            </div>
            <span style={{ fontSize: "0.75rem", color: i === stepIdx ? "#e0e0e8" : "#8888a0" }}>{s.label}</span>
            {i < STEPS.length - 1 && <div style={styles.stepLine} />}
          </div>
        ))}
      </div>

      {/* ================================================================ */}
      {/* STEP 1: Select Agent & Repo */}
      {/* ================================================================ */}
      {step === "select-agent" && (
        <>
          <h2 style={styles.title}>Select Agent & Repository</h2>
          <p style={styles.subtitle}>Choose an agent and repository to analyze for deployment configuration.</p>

          {/* Agent selection */}
          <div style={styles.card}>
            <span style={styles.cardTitle}>Agent</span>
            {agents.length === 0 ? (
              <p style={{ fontSize: "0.85rem", color: "#8888a0", margin: 0 }}>No agents available. Create one in Settings.</p>
            ) : (
              <select
                style={styles.select}
                value={selectedAgentId}
                onChange={(e) => setSelectedAgentId(e.target.value)}
              >
                <option value="">Select an agent...</option>
                {agents.filter(a => a.isActive).map((agent) => (
                  <option key={agent.id} value={agent.id}>
                    {agent.displayName || agent.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {/* Repo selection from agent mounts */}
          {selectedAgentId && agentMounts.length > 0 && (
            <div style={styles.card}>
              <span style={styles.cardTitle}>Repository</span>
              <select
                style={styles.select}
                value={selectedRepoId}
                onChange={(e) => setSelectedRepoId(e.target.value)}
              >
                <option value="">Select a repository...</option>
                {agentMounts.map((mount) => (
                  <option key={mount.id} value={mount.mountName || mount.hostPath}>
                    {mount.mountName || mount.hostPath}
                  </option>
                ))}
              </select>
            </div>
          )}

          {selectedAgentId && agentMounts.length === 0 && (
            <p style={{ fontSize: "0.8rem", color: "#666680", fontStyle: "italic" }}>
              No repos mounted on this agent. Discovery will analyze the agent&apos;s workspace.
            </p>
          )}

          {/* Optional: Target server */}
          <div style={styles.card}>
            <span style={styles.cardTitle}>Target Server (optional)</span>
            <p style={{ fontSize: "0.8rem", color: "#8888a0", margin: 0 }}>Optionally select a server to deploy to.</p>
            {servers.length === 0 ? (
              <p style={{ fontSize: "0.8rem", color: "#666680", fontStyle: "italic", margin: 0 }}>No servers connected.</p>
            ) : (
              <select
                style={styles.select}
                value={selectedServerId}
                onChange={(e) => setSelectedServerId(e.target.value)}
              >
                <option value="">None</option>
                {servers.map(srv => (
                  <option key={srv.id} value={srv.id}>{srv.display_name} ({srv.status})</option>
                ))}
              </select>
            )}
            <button style={{ ...styles.addServerBtn, alignSelf: "flex-start" }} onClick={() => setShowAddModal(true)}>
              + Add New Server
            </button>
          </div>

          {/* Optional: Manual repo URL */}
          <div style={styles.card}>
            <span style={styles.cardTitle}>Repository URL (optional)</span>
            <p style={{ fontSize: "0.8rem", color: "#8888a0", margin: 0 }}>Override with a specific repo URL for discovery.</p>
            {knownRepoUrls.length > 0 && (
              <select
                style={styles.select}
                value={selectedRepoUrl}
                onChange={(e) => setSelectedRepoUrl(e.target.value)}
              >
                <option value="">None</option>
                {knownRepoUrls.map(url => (
                  <option key={url} value={url}>{url}</option>
                ))}
              </select>
            )}
            {!showManualRepoInput ? (
              <button style={{ ...styles.addServerBtn, alignSelf: "flex-start" }} onClick={() => setShowManualRepoInput(true)}>
                + Add Repo URL
              </button>
            ) : (
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <input
                  style={{ ...styles.input, flex: 1 }}
                  value={manualRepoUrl}
                  onChange={e => setManualRepoUrl(e.target.value)}
                  placeholder="https://github.com/org/repo.git"
                  onKeyDown={e => {
                    if (e.key === "Enter" && manualRepoUrl.trim()) {
                      setSelectedRepoUrl(manualRepoUrl.trim());
                      setShowManualRepoInput(false);
                    }
                  }}
                />
                <button
                  style={{ ...styles.primaryButton, padding: "8px 14px", fontSize: "0.8rem" }}
                  onClick={() => {
                    if (manualRepoUrl.trim()) {
                      setSelectedRepoUrl(manualRepoUrl.trim());
                      setShowManualRepoInput(false);
                    }
                  }}
                >
                  Add
                </button>
                <button
                  style={{ ...styles.secondaryButton, padding: "8px 14px", fontSize: "0.8rem" }}
                  onClick={() => setShowManualRepoInput(false)}
                >
                  Cancel
                </button>
              </div>
            )}
            {selectedRepoUrl && (
              <div style={{ fontSize: "0.8rem", color: "#6cffa0" }}>
                Selected: {selectedRepoUrl}
                <button
                  style={{ marginLeft: 8, background: "none", border: "none", color: "#8888a0", cursor: "pointer", fontSize: "0.75rem" }}
                  onClick={() => setSelectedRepoUrl("")}
                >
                  clear
                </button>
              </div>
            )}
          </div>

          {showAddModal && (
            <AddServerModal
              environments={environments}
              onComplete={(result) => {
                setShowAddModal(false);
                setSelectedServerId(result.resource_id);
              }}
              onCancel={() => setShowAddModal(false)}
            />
          )}
        </>
      )}

      {/* ================================================================ */}
      {/* STEP 2: Discovery */}
      {/* ================================================================ */}
      {step === "discovery" && (
        <AgentDiscoveryView
          agentId={selectedAgentId}
          repoId={selectedRepoId}
          environment={selectedEnv || "dev"}
          onComplete={(state, _completeness) => {
            setAgentDiscoveryState(state);
            saveProgress({ agentDiscoveryState: state, step: "review" });
            goNext();
          }}
          onCancel={onCancel}
          onStateChange={(state, msgs) => {
            if (state) setAgentDiscoveryState(state);
            if (msgs) setConversationMessages(msgs);
          }}
        />
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
                    <span style={{ color: "#8888a0", fontSize: "0.8rem" }}>{isOpen ? "\u25BE" : "\u25B8"}</span>
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

          {draftComponents.length > 0 && (
            <div style={styles.card}>
              <span style={styles.cardTitle}>Register as Components</span>
              <p style={{ fontSize: "0.8rem", color: "#8888a0", margin: 0 }}>Toggle which discovered items to register as managed components.</p>

              {draftComponents.map((draft, i) => (
                <div key={i} style={{ display: "flex", gap: 10, alignItems: "center", padding: "6px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" }}>
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
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <label style={{ fontSize: "0.75rem", color: "#8888a0" }}>Assign Environment</label>
              <select style={styles.select} value={selectedEnv} onChange={e => setSelectedEnv(e.target.value)}>
                {environments.map(env => (
                  <option key={env.name} value={env.name}>{env.display_name}</option>
                ))}
              </select>
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
                <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                  <label style={{ fontSize: "0.75rem", color: "#8888a0" }}>Check Interval (seconds)</label>
                  <input style={{ ...styles.input, maxWidth: 120 }} type="number" value={monitoringInterval} onChange={e => setMonitoringInterval(parseInt(e.target.value) || 60)} />
                </div>

                <span style={{ fontSize: "0.75rem", color: "#8888a0", marginTop: 4 }}>Components to monitor:</span>
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
          <h2 style={styles.title}>Discovery Complete</h2>
          <div style={{ ...styles.card, borderColor: "#6cffa022" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
              <span style={{ color: "#6cffa0", fontSize: "1.3rem" }}>{"\u2713"}</span>
              <span style={{ color: "#6cffa0", fontWeight: 600, fontSize: "1rem" }}>{selectedServer?.display_name || selectedServerId} is ready</span>
            </div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: "0.7rem", color: "#8888a0" }}>Environment</span><span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>{selectedEnv}</span></div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: "0.7rem", color: "#8888a0" }}>Manifest</span><span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>{manifestName || "\u2014"}</span></div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: "0.7rem", color: "#8888a0" }}>Scripts</span><span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>{generatedScripts.length || "None"}</span></div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}><span style={{ fontSize: "0.7rem", color: "#8888a0" }}>Monitoring</span><span style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>{monitoringEnabled ? `Every ${monitoringInterval}s` : "Off"}</span></div>
            </div>
          </div>

          <button style={styles.primaryButton} onClick={() => { clearProgress(); onComplete(); }}>Done</button>
        </>
      )}

      {/* Navigation Footer */}
      <div style={step === "done" ? { display: "none" } : styles.navRow}>
        {stepIdx > 0 ? (
          <button style={styles.secondaryButton} onClick={goBack}>Back</button>
        ) : (
          <button style={styles.secondaryButton} onClick={onCancel}>Cancel</button>
        )}
        {step === "review" ? (
          <button
            style={{ ...styles.primaryButton, opacity: creatingComponents ? 0.4 : 1 }}
            onClick={createComponentsAndProceed}
            disabled={creatingComponents}
          >
            {creatingComponents ? "Creating Components..." : "Continue"}
          </button>
        ) : step === "scripts" ? (
          <button style={styles.secondaryButton} onClick={goNext}>
            {generatedScripts.length > 0 ? "Continue" : "Skip"}
          </button>
        ) : (
          <button
            style={{ ...styles.primaryButton, opacity: canNext() ? 1 : 0.4 }}
            onClick={goNext}
            disabled={!canNext()}
          >
            Continue
          </button>
        )}
      </div>
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 12,
    padding: 16,
    display: "flex",
    flexDirection: "column",
    gap: 10,
  },
  cardTitle: { fontSize: "0.8rem", fontWeight: 600, color: "#8888a0", textTransform: "uppercase" as const },
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
  serverGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
    gap: 10,
  },
  serverCard: {
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 10,
    padding: 14,
    cursor: "pointer",
    display: "flex",
    flexDirection: "column",
    gap: 6,
    transition: "border-color 0.2s",
  },
  addServerBtn: {
    backgroundColor: "#1a1a2e",
    color: "#6c8aff",
    borderWidth: "1px", borderStyle: "dashed", borderColor: "#3a3a4e",
    borderRadius: 8,
    padding: "10px",
    fontSize: "0.85rem",
    cursor: "pointer",
    textAlign: "center",
    alignSelf: "flex-start",
  },
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
  reviewItem: { display: "flex", gap: 8, alignItems: "baseline", padding: "4px 0", borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e" },
  codeBlock: {
    backgroundColor: "#0a0a12",
    color: "#e0e0e8",
    padding: 12,
    borderRadius: 8,
    fontSize: "0.75rem",
    overflow: "auto" as const,
    maxHeight: 300,
    margin: 0,
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    fontFamily: "monospace",
  },
  navRow: { display: "flex", justifyContent: "space-between", alignItems: "center", paddingTop: 4 },
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
};
