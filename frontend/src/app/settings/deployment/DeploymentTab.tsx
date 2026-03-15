import React, { useEffect, useState, useCallback } from "react";
import { BACKEND_API, GATEWAY_API } from "@/lib/config";
import SetupWizard from "./SetupWizard";
import AgentCardGrid from "./AgentCardGrid";
import SharedSettingsForm from "./SharedSettingsForm";
import SingleAgentEditor from "./SingleAgentEditor";
import PipelineSection from "./PipelineSection";
import QuickDeployForm from "./QuickDeployForm";

interface WorkspaceMount {
  id?: string;
  host_path: string;
  mount_name: string;
  container_path: string;
  readonly: boolean;
}

interface ChannelConfig {
  channel: string;
  enabled: boolean;
  sandbox_override: string | null;
}

interface Agent {
  id: string;
  name: string;
  display_name: string;
  system_prompt: string;
  model: string;
  utility_model: string;
  sandbox_image: string | null;
  is_active: boolean;
  workspace_mounts: WorkspaceMount[];
  channels: ChannelConfig[];
}

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

type ViewMode = "loading" | "empty" | "dashboard" | "edit-one" | "edit-all" | "quick-deploy";

export default function DeploymentTab() {
  const [view, setView] = useState<ViewMode>("loading");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [allAgents, setAllAgents] = useState<Agent[]>([]);
  const [environments, setEnvironments] = useState<Environment[]>(DEFAULT_ENVIRONMENTS);
  const [availableModels, setAvailableModels] = useState<{ id: string; name: string }[]>([]);
  const [sandboxImages, setSandboxImages] = useState<string[]>([]);
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [msg, setMsg] = useState("");

  // Shared settings
  const [shared, setShared] = useState({
    model: "anthropic/claude-sonnet-4-20250514",
    utility_model: "anthropic/claude-sonnet-4-20250514",
    sandbox_image: "",
  });

  const fetchData = useCallback(async () => {
    try {
      // Fetch agents
      const agentsRes = await fetch(`${BACKEND_API}/agents`);
      const allAgentsList: Agent[] = agentsRes.ok ? await agentsRes.json() : [];
      setAllAgents(allAgentsList);

      const deployAgents = allAgentsList.filter((a) => a.name.startsWith("deploy-"));
      setAgents(deployAgents);

      // Fetch environments
      try {
        const envRes = await fetch(`${GATEWAY_API}/deployments/environments`);
        if (envRes.ok) {
          const envData = await envRes.json();
          if (Array.isArray(envData) && envData.length > 0) {
            setEnvironments(envData);
          }
        }
      } catch { /* use defaults */ }

      // Fetch shared settings
      try {
        const settingsRes = await fetch(`${BACKEND_API}/settings`);
        if (settingsRes.ok) {
          const settings = await settingsRes.json();
          setShared({
            model: settings["deployment.shared.model"] || "anthropic/claude-sonnet-4-20250514",
            utility_model: settings["deployment.shared.utility_model"] || "anthropic/claude-sonnet-4-20250514",
            sandbox_image: settings["deployment.shared.sandbox_image"] || "",
          });
        }
      } catch { /* use defaults */ }

      // Fetch models
      try {
        const modelsRes = await fetch(`${BACKEND_API}/settings/llm/models`);
        if (modelsRes.ok) setAvailableModels(await modelsRes.json());
      } catch { /* use defaults */ }

      // Fetch sandbox images
      try {
        const imagesRes = await fetch(`${BACKEND_API}/agents/sandbox-images`);
        if (imagesRes.ok) setSandboxImages(await imagesRes.json());
      } catch { /* use defaults */ }

      setView(deployAgents.length > 0 ? "dashboard" : "empty");
    } catch {
      setView("empty");
    }
  }, []);

  useEffect(() => { fetchData(); }, [fetchData]);

  // Collect workspace mounts from existing non-deploy agents for wizard
  const existingMounts: WorkspaceMount[] = [];
  const seenPaths = new Set<string>();
  for (const a of allAgents) {
    if (a.name.startsWith("deploy-")) continue;
    for (const m of a.workspace_mounts || []) {
      const key = `${m.host_path}:${m.container_path}`;
      if (!seenPaths.has(key)) {
        seenPaths.add(key);
        existingMounts.push({ ...m, readonly: true });
      }
    }
  }

  const envNames = environments.map((e) => e.name);

  // Compute override warning for edit-all mode
  const overriddenAgents = agents.filter(
    (a) => a.model !== shared.model || a.utility_model !== shared.utility_model
  );
  const overrideWarning = overriddenAgents.length > 0
    ? `${overriddenAgents.length} agent(s) have overrides (${overriddenAgents.map((a) => a.display_name || a.name).join(", ")}). These will NOT be changed.`
    : undefined;

  const saveSharedSettings = async () => {
    setMsg("");
    try {
      const entries = [
        { key: "deployment.shared.model", value: shared.model },
        { key: "deployment.shared.utility_model", value: shared.utility_model },
        { key: "deployment.shared.sandbox_image", value: shared.sandbox_image },
      ];
      for (const entry of entries) {
        await fetch(`${BACKEND_API}/settings/${entry.key}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: entry.value }),
        });
      }

      // Update non-overridden agents
      for (const agent of agents) {
        const hasModelOverride = overriddenAgents.some((a) => a.id === agent.id);
        if (!hasModelOverride) {
          await fetch(`${BACKEND_API}/agents/${agent.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              name: agent.name,
              display_name: agent.display_name,
              system_prompt: agent.system_prompt,
              model: shared.model,
              utility_model: shared.utility_model,
              sandbox_image: shared.sandbox_image || agent.sandbox_image,
              workspace_mounts: (agent.workspace_mounts || []).map((m) => ({
                host_path: m.host_path,
                mount_name: m.mount_name,
                container_path: m.container_path,
                readonly: true,
              })),
              channels: (agent.channels || []).map((c) => ({
                channel: c.channel,
                enabled: c.enabled,
                sandbox_override: c.sandbox_override,
              })),
            }),
          });
        }
      }
      setMsg("Shared settings saved.");
      await fetchData();
    } catch {
      setMsg("Failed to save shared settings.");
    }
  };

  const resetAllOverrides = async () => {
    setMsg("");
    try {
      for (const agent of agents) {
        await fetch(`${BACKEND_API}/agents/${agent.id}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: agent.name,
            display_name: agent.display_name,
            system_prompt: agent.system_prompt,
            model: shared.model,
            utility_model: shared.utility_model,
            sandbox_image: shared.sandbox_image || agent.sandbox_image,
            workspace_mounts: (agent.workspace_mounts || []).map((m) => ({
              host_path: m.host_path,
              mount_name: m.mount_name,
              container_path: m.container_path,
              readonly: true,
            })),
            channels: (agent.channels || []).map((c) => ({
              channel: c.channel,
              enabled: c.enabled,
              sandbox_override: c.sandbox_override,
            })),
          }),
        });
      }
      setMsg("All overrides reset.");
      await fetchData();
    } catch {
      setMsg("Failed to reset overrides.");
    }
  };

  if (view === "loading") {
    return <div style={{ color: "#8888a0", padding: "24px" }}>Loading deployment agents...</div>;
  }

  if (view === "quick-deploy") {
    return (
      <QuickDeployForm
        environments={environments}
        onBack={() => setView(agents.length > 0 ? "dashboard" : "empty")}
        onDeployed={() => fetchData()}
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
          onCreated={() => fetchData()}
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
        agent={editingAgent as Agent & { system_prompt: string; sandbox_image: string | null; workspace_mounts: WorkspaceMount[]; channels: ChannelConfig[] }}
        sharedModel={shared.model}
        sharedUtilityModel={shared.utility_model}
        availableModels={availableModels}
        onBack={() => { setEditingAgent(null); setView("dashboard"); }}
        onSaved={() => { setEditingAgent(null); setView("dashboard"); fetchData(); }}
      />
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
      {view === "edit-all" && (
        <>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <h2 style={{ fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 }}>
              Deployment Agents — Edit Shared Settings
            </h2>
            <button
              style={styles.secondaryButton}
              onClick={() => setView("dashboard")}
            >
              Cancel Edit
            </button>
          </div>
          <SharedSettingsForm
            settings={shared}
            onChange={setShared}
            availableModels={availableModels}
            sandboxImages={sandboxImages}
            overrideWarning={overrideWarning}
            onSave={saveSharedSettings}
            onResetAll={resetAllOverrides}
          />
        </>
      )}

      {(view === "dashboard" || view === "edit-all") && (
        <>
          {view === "dashboard" && (
            <>
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "-8px" }}>
              <button style={styles.secondaryButton} onClick={() => setView("quick-deploy")}>
                Quick Deploy
              </button>
            </div>
            <AgentCardGrid
              agents={agents}
              environments={environments}
              sharedModel={shared.model}
              sharedUtilityModel={shared.utility_model}
              sharedSandboxImage={shared.sandbox_image}
              onEditAgent={(agent) => { const full = agents.find((a) => a.id === agent.id); if (full) { setEditingAgent(full); setView("edit-one"); } }}
              onEditAll={() => setView("edit-all")}
            />
            </>
          )}

          <PipelineSection environmentNames={envNames} />
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
};
