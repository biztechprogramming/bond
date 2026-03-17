import React, { useEffect, useState, useCallback, useMemo } from "react";
import { BACKEND_API, GATEWAY_API } from "@/lib/config";
import { useAgentsWithRelations, useAvailableModels, useSettingsMap, callReducer, type AgentWithRelations } from "@/hooks/useSpacetimeDB";
import { getAgents } from "@/lib/spacetimedb-client";
import SetupWizard from "./SetupWizard";
import OnboardServerWizard from "./OnboardServerWizard";
import AddServerModal from "./AddServerModal";
import DiscoverStackWizard from "./DiscoverStackWizard";
import AgentCardGrid from "./AgentCardGrid";
import SharedSettingsForm from "./SharedSettingsForm";
import SingleAgentEditor from "./SingleAgentEditor";
import PipelineSection from "./PipelineSection";
import QuickDeployForm from "./QuickDeployForm";
import ScriptRegistration from "./ScriptRegistration";
import EnvironmentDashboard from "./EnvironmentDashboard";
import DeploymentTimeline from "./DeploymentTimeline";
import AlertRulesEditor from "./AlertRulesEditor";
import SecretManager from "./SecretManager";
import CompareEnvironments from "./CompareEnvironments";
import ComponentDetail from "./ComponentDetail";
import AddComponentForm from "./AddComponentForm";
import InfraMap from "./InfraMap";

interface Environment {
  name: string;
  display_name: string;
}

const DEFAULT_ENVIRONMENTS: Environment[] = [
  { name: "dev", display_name: "Development" },
  { name: "qa", display_name: "QA" },
  { name: "staging", display_name: "Staging" },
  { name: "uat", display_name: "UAT" },
  { name: "prod", display_name: "Production" },
];

type ViewMode =
  | "loading" | "empty" | "dashboard" | "edit-one" | "edit-all"
  | "quick-deploy" | "register-script" | "onboard-server"
  | "script-from-discovery" | "monitoring-setup" | "live-logs"
  | "alert-rules" | "secrets" | "compare-envs" | "infra-map" | "timeline"
  | "agent-settings" | "component-detail" | "add-server" | "discover" | "add-component";

type TopTab = "env" | "map" | "timeline";

const ENV_TAB_LABELS: Record<string, string> = {
  dev: "Dev",
  qa: "QA",
  staging: "Staging",
  uat: "UAT",
  prod: "Prod",
};

export default function DeploymentTab() {
  const [view, setView] = useState<ViewMode>("loading");
  const [topTab, setTopTab] = useState<TopTab>("env");
  const [selectedEnvironment, setSelectedEnvironment] = useState<string>("");
  const [environments, setEnvironments] = useState<Environment[]>(DEFAULT_ENVIRONMENTS);
  const [sandboxImages, setSandboxImages] = useState<string[]>([]);
  const [editingAgent, setEditingAgent] = useState<AgentWithRelations | null>(null);
  const [selectedComponentId, setSelectedComponentId] = useState<string | null>(null);
  const [msg, setMsg] = useState("");
  const [initialized, setInitialized] = useState(false);
  const [showAddServerModal, setShowAddServerModal] = useState(false);

  // SpacetimeDB subscriptions (reactive, no fetch needed)
  const allAgents = useAgentsWithRelations();
  const settingsMap = useSettingsMap();
  const availableModels = useAvailableModels();

  const agents = useMemo(() => allAgents.filter(a => a.name.startsWith("deploy-")), [allAgents]);

  const shared = useMemo(() => ({
    model: settingsMap["deployment.shared.model"] || "anthropic/claude-sonnet-4-20250514",
    utility_model: settingsMap["deployment.shared.utility_model"] || "anthropic/claude-sonnet-4-20250514",
    sandbox_image: settingsMap["deployment.shared.sandbox_image"] || "",
  }), [settingsMap]);
  const [sharedOverride, setSharedOverride] = useState<{ model: string; utility_model: string; sandbox_image: string } | null>(null);
  const effectiveShared = sharedOverride ?? shared;

  // REST-only fetches (sandbox images, environments)
  const fetchExceptions = useCallback(async () => {
    try {
      const envRes = await fetch(`${GATEWAY_API}/deployments/environments`);
      if (envRes.ok) {
        const envData = await envRes.json();
        if (Array.isArray(envData) && envData.length > 0) {
          setEnvironments(envData);
          setSelectedEnvironment(prev => prev || envData[0].name);
        }
      }
    } catch { /* use defaults */ }

    try {
      const imagesRes = await fetch(`${BACKEND_API}/agents/sandbox-images`);
      if (imagesRes.ok) setSandboxImages(await imagesRes.json());
    } catch { /* use defaults */ }
  }, []);

  useEffect(() => { fetchExceptions(); }, [fetchExceptions]);

  // Set initial view once data arrives
  useEffect(() => {
    if (!initialized && allAgents.length >= 0) {
      if (!selectedEnvironment) setSelectedEnvironment(DEFAULT_ENVIRONMENTS[0].name);
      setView(agents.length > 0 ? "dashboard" : "empty");
      setInitialized(true);
    }
  }, [allAgents, agents.length, initialized, selectedEnvironment]);

  // Keep view in sync when agents change after init
  useEffect(() => {
    if (initialized && view === "empty" && agents.length > 0) setView("dashboard");
    if (initialized && view === "dashboard" && agents.length === 0) setView("empty");
  }, [agents.length, initialized, view]);

  // Collect workspace mounts from existing non-deploy agents for wizard
  const existingMounts = useMemo(() => {
    const mounts: AgentWithRelations["workspace_mounts"] = [];
    const seenPaths = new Set<string>();
    for (const a of allAgents) {
      if (a.name.startsWith("deploy-")) continue;
      for (const m of a.workspace_mounts || []) {
        const key = `${m.host_path}:${m.container_path}`;
        if (!seenPaths.has(key)) {
          seenPaths.add(key);
          mounts.push({ ...m, readonly: true });
        }
      }
    }
    return mounts;
  }, [allAgents]);

  const envNames = environments.map((e) => e.name);

  // Compute override warning for edit-all mode
  const overriddenAgents = agents.filter(
    (a) => a.model !== effectiveShared.model || a.utility_model !== effectiveShared.utility_model
  );
  const overrideWarning = overriddenAgents.length > 0
    ? `${overriddenAgents.length} agent(s) have overrides (${overriddenAgents.map((a) => a.display_name || a.name).join(", ")}). These will NOT be changed.`
    : undefined;

  const updateAgentViaReducer = (agent: AgentWithRelations, model: string, utilityModel: string, sandboxImage: string) => {
    const agentRow = getAgents().find(r => r.id === agent.id);
    callReducer(conn => conn.reducers.updateAgent({
      id: agent.id,
      name: agent.name,
      displayName: agent.display_name,
      systemPrompt: agent.system_prompt,
      model,
      utilityModel,
      tools: agentRow?.tools || "[]",
      sandboxImage: sandboxImage || agent.sandbox_image || "",
      maxIterations: agentRow?.maxIterations ?? 50,
      isActive: agent.is_active,
      isDefault: agent.is_default,
    }));
  };

  const saveSharedSettings = () => {
    setMsg("");
    try {
      if (!callReducer(conn => {
        const entries = [
          { key: "deployment.shared.model", value: effectiveShared.model },
          { key: "deployment.shared.utility_model", value: effectiveShared.utility_model },
          { key: "deployment.shared.sandbox_image", value: effectiveShared.sandbox_image },
        ];
        for (const entry of entries) {
          conn.reducers.setSetting({ key: entry.key, value: entry.value, keyType: "string" });
        }
      })) {
        setMsg("Not connected to SpacetimeDB.");
        return;
      }

      for (const agent of agents) {
        const hasModelOverride = overriddenAgents.some((a) => a.id === agent.id);
        if (!hasModelOverride) {
          updateAgentViaReducer(agent, effectiveShared.model, effectiveShared.utility_model, effectiveShared.sandbox_image);
        }
      }
      setSharedOverride(null);
      setMsg("Shared settings saved.");
    } catch {
      setMsg("Failed to save shared settings.");
    }
  };

  const resetAllOverrides = () => {
    setMsg("");
    try {
      for (const agent of agents) {
        updateAgentViaReducer(agent, effectiveShared.model, effectiveShared.utility_model, effectiveShared.sandbox_image);
      }
      setMsg("All overrides reset.");
    } catch {
      setMsg("Failed to reset overrides.");
    }
  };

  const goToDashboard = () => setView(agents.length > 0 ? "dashboard" : "empty");

  // Handle navigation from EnvironmentDashboard
  const handleEnvNavigate = (navView: string, params?: Record<string, any>) => {
    switch (navView) {
      case "alert-rules": setView("alert-rules"); break;
      case "secrets": setView("secrets"); break;
      case "compare-envs": setView("compare-envs"); break;
      case "live-logs": setView("live-logs"); break;
      case "monitoring-setup": setView("monitoring-setup"); break;
      case "onboard-server": setShowAddServerModal(true); break;
      case "add-server": setShowAddServerModal(true); break;
      case "discover": setView("discover"); break;
      case "script-from-discovery": setView("script-from-discovery"); break;
      case "add-component": setView("add-component"); break;
      case "component-detail":
        if (params?.componentId) {
          setSelectedComponentId(params.componentId);
          setView("component-detail");
        }
        break;
      default:
        // Only navigate to views that have render handlers; ignore unimplemented ones
        const implemented: ViewMode[] = [
          "dashboard", "edit-one", "edit-all", "quick-deploy", "register-script",
          "onboard-server", "script-from-discovery", "monitoring-setup", "live-logs",
          "alert-rules", "secrets", "compare-envs", "infra-map", "timeline",
          "agent-settings", "component-detail", "add-server", "discover", "add-component",
        ];
        if (implemented.includes(navView as ViewMode)) {
          setView(navView as ViewMode);
        }
        break;
    }
  };

  if (view === "loading") {
    return <div style={{ color: "#8888a0", padding: "24px" }}>Loading deployment agents...</div>;
  }

  if (view === "quick-deploy") {
    return (
      <QuickDeployForm
        environments={environments}
        onBack={goToDashboard}
        onDeployed={goToDashboard}
      />
    );
  }

  if (view === "register-script") {
    return (
      <ScriptRegistration
        onBack={goToDashboard}
        onRegistered={goToDashboard}
      />
    );
  }

  if (view === "empty") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        <SetupWizard
          environments={environments}
          availableModels={availableModels}
          sandboxImages={sandboxImages}
          existingMounts={existingMounts}
          onCreated={goToDashboard}
        />
        <div style={{ borderTop: "1px solid #1e1e2e", paddingTop: "16px", display: "flex", alignItems: "center", gap: "12px" }}>
          <span style={{ fontSize: "0.85rem", color: "#8888a0" }}>Just want to deploy quickly?</span>
          <button style={styles.secondaryButton} onClick={() => setView("quick-deploy")}>
            Quick Deploy
          </button>
        </div>
      </div>
    );
  }

  if (view === "edit-one" && editingAgent) {
    return (
      <SingleAgentEditor
        agent={editingAgent}
        sharedModel={shared.model}
        sharedUtilityModel={shared.utility_model}
        availableModels={availableModels}
        onBack={() => { setEditingAgent(null); setView("dashboard"); }}
        onSaved={() => { setEditingAgent(null); setView("dashboard"); }}
      />
    );
  }

  if (view === "alert-rules") {
    return <AlertRulesEditor environment={selectedEnvironment} onBack={goToDashboard} />;
  }

  if (view === "secrets") {
    return <SecretManager environment={selectedEnvironment} onBack={goToDashboard} />;
  }

  if (view === "compare-envs") {
    return <CompareEnvironments environments={environments} onBack={goToDashboard} />;
  }

  if (view === "add-component") {
    return <AddComponentForm onComplete={goToDashboard} onCancel={goToDashboard} />;
  }

  if (view === "component-detail" && selectedComponentId) {
    return <ComponentDetail componentId={selectedComponentId} onBack={goToDashboard} onNavigate={handleEnvNavigate} />;
  }

  // Main dashboard view with environment tabs
  const currentEnv = environments.find((e) => e.name === selectedEnvironment) || environments[0];

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {/* Top navigation: environment tabs + special tabs */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <div style={{ display: "flex", gap: "2px", backgroundColor: "#1a1a2e", borderRadius: "8px", padding: "2px", border: "1px solid #3a3a4e" }}>
          {environments.map((env) => (
            <button
              key={env.name}
              onClick={() => { setTopTab("env"); setSelectedEnvironment(env.name); setView("dashboard"); }}
              style={{
                ...styles.tabButton,
                backgroundColor: topTab === "env" && selectedEnvironment === env.name ? "#6c8aff" : "transparent",
                color: topTab === "env" && selectedEnvironment === env.name ? "#fff" : "#8888a0",
              }}
            >
              {ENV_TAB_LABELS[env.name] || env.display_name}
            </button>
          ))}
          <div style={{ width: "1px", backgroundColor: "#3a3a4e", margin: "4px 2px" }} />
          <button
            onClick={() => { setTopTab("map"); setView("infra-map"); }}
            style={{
              ...styles.tabButton,
              backgroundColor: topTab === "map" ? "#6c8aff" : "transparent",
              color: topTab === "map" ? "#fff" : "#8888a0",
            }}
          >
            Map
          </button>
          <button
            onClick={() => { setTopTab("timeline"); setView("timeline"); }}
            style={{
              ...styles.tabButton,
              backgroundColor: topTab === "timeline" ? "#6c8aff" : "transparent",
              color: topTab === "timeline" ? "#fff" : "#8888a0",
            }}
          >
            &#9201;
          </button>
        </div>
        <div style={{ display: "flex", gap: "8px" }}>
          <button style={styles.secondaryButton} onClick={() => setShowAddServerModal(true)}>+ Server</button>
          <button style={styles.secondaryButton} onClick={() => setView("discover")}>Discover</button>
          <button style={styles.secondaryButton} onClick={() => setView("register-script")}>Register Script</button>
          <button style={styles.secondaryButton} onClick={() => setView("quick-deploy")}>Quick Deploy</button>
          <button
            style={{ ...styles.secondaryButton, fontSize: "0.8rem", color: "#8888a0" }}
            onClick={() => setView("agent-settings")}
          >
            Agent Settings
          </button>
        </div>
      </div>

      {/* Edit-all shared settings */}
      {view === "edit-all" && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
              Deployment Agents — Edit Shared Settings
            </h2>
            <button style={styles.secondaryButton} onClick={() => setView("dashboard")}>Cancel Edit</button>
          </div>
          <SharedSettingsForm
            settings={shared}
            onChange={setSharedOverride}
            availableModels={availableModels}
            sandboxImages={sandboxImages}
            overrideWarning={overrideWarning}
            onSave={saveSharedSettings}
            onResetAll={resetAllOverrides}
          />
        </>
      )}

      {/* Discover stack wizard */}
      {view === "discover" && (
        <DiscoverStackWizard
          environments={environments}
          onComplete={goToDashboard}
          onCancel={goToDashboard}
        />
      )}

      {/* Legacy onboard-server view — redirects to discover */}
      {view === "onboard-server" && (
        <DiscoverStackWizard
          environments={environments}
          onComplete={goToDashboard}
          onCancel={goToDashboard}
        />
      )}

      {/* Add server modal overlay */}
      {showAddServerModal && (
        <AddServerModal
          environments={environments}
          onComplete={() => { setShowAddServerModal(false); goToDashboard(); }}
          onCancel={() => setShowAddServerModal(false)}
        />
      )}

      {/* Infra map — server management with environment checkboxes */}
      {view === "infra-map" && (
        <InfraMap onAddServer={() => setShowAddServerModal(true)} />
      )}

      {/* Timeline view */}
      {view === "timeline" && (
        <DeploymentTimeline environments={environments} />
      )}

      {/* Agent settings (moved from old dashboard default) */}
      {view === "agent-settings" && (
        <>
          <AgentCardGrid
            agents={agents}
            environments={environments}
            sharedModel={shared.model}
            sharedUtilityModel={shared.utility_model}
            sharedSandboxImage={shared.sandbox_image}
            onEditAgent={(agent) => { const full = agents.find((a) => a.id === agent.id); if (full) { setEditingAgent(full); setView("edit-one"); } }}
            onEditAll={() => setView("edit-all")}
          />
          <PipelineSection environmentNames={envNames} />
        </>
      )}

      {/* Environment dashboard (default dashboard view) */}
      {(view === "dashboard" || view === "edit-all") && topTab === "env" && currentEnv && (
        <>
          <EnvironmentDashboard
            environment={currentEnv}
            agents={agents}
            onNavigate={handleEnvNavigate}
          />
          {view === "dashboard" && <PipelineSection environmentNames={envNames} />}
        </>
      )}

      {msg && <div style={{ fontSize: "0.85rem", color: msg.includes("error") || msg.includes("Failed") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  secondaryButton: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  tabButton: {
    border: "none",
    borderRadius: "6px",
    padding: "6px 14px",
    fontSize: "0.8rem",
    fontWeight: 600,
    cursor: "pointer",
    transition: "background-color 0.15s",
  },
};
