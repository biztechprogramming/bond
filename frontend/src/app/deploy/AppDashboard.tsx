"use client";

import React, { useMemo } from "react";
import { useAgentsWithRelations, useResources, useComponents } from "@/hooks/useSpacetimeDB";
import AppCard, { type AppInfo } from "./AppCard";

interface Props {
  onSelectApp: (id: string) => void;
  onNewDeploy: () => void;
}

// Translate agent-centric data into app-centric view
function buildAppList(agents: ReturnType<typeof useAgentsWithRelations>, resources: ReturnType<typeof useResources>, components: ReturnType<typeof useComponents>): AppInfo[] {
  // Group deploy agents by app name (strip "deploy-" prefix and env suffix)
  const appMap = new Map<string, AppInfo>();

  for (const agent of agents) {
    if (!agent.name.startsWith("deploy-")) continue;

    // Extract app name — agents are named like "deploy-myapp-prod"
    const parts = agent.name.replace("deploy-", "").split("-");
    const envName = parts.pop() || "dev";
    const appName = parts.join("-") || agent.display_name || agent.name;

    if (!appMap.has(appName)) {
      appMap.set(appName, {
        id: appName,
        name: agent.display_name || appName,
        framework: "Unknown",
        environments: [],
        lastDeployTime: null,
        healthStatus: "unknown",
      });
    }

    const app = appMap.get(appName)!;
    app.environments.push({
      name: envName,
      status: agent.is_active ? "healthy" : "inactive",
    });
  }

  // Enrich with component data if available
  for (const comp of components) {
    const appName = comp.name.replace("deploy-", "");
    const app = appMap.get(appName);
    if (app && comp.framework) {
      app.framework = comp.framework;
    }
  }

  return Array.from(appMap.values());
}

export default function AppDashboard({ onSelectApp, onNewDeploy }: Props) {
  const agents = useAgentsWithRelations();
  const resources = useResources();
  const components = useComponents();

  const apps = useMemo(() => buildAppList(agents, resources, components), [agents, resources, components]);

  return (
    <div>
      <div style={s.toolbar}>
        <h2 style={s.heading}>Applications</h2>
        <button style={s.deployBtn} onClick={onNewDeploy}>
          + Deploy New App
        </button>
      </div>

      {apps.length === 0 ? (
        <div style={s.empty}>
          <div style={s.emptyIcon}>&#x1F680;</div>
          <p style={s.emptyTitle}>No apps deployed yet</p>
          <p style={s.emptyDesc}>Get started by deploying your first application.</p>
          <button style={s.deployBtn} onClick={onNewDeploy}>Deploy New App</button>
        </div>
      ) : (
        <div style={s.grid}>
          {apps.map((app) => (
            <AppCard key={app.id} app={app} onClick={() => onSelectApp(app.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  toolbar: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" },
  heading: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", margin: 0 },
  deployBtn: {
    backgroundColor: "#6c8aff", color: "#fff", border: "none", borderRadius: "8px",
    padding: "10px 20px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer",
  },
  grid: {
    display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "16px",
  },
  empty: { textAlign: "center", padding: "60px 20px" },
  emptyIcon: { fontSize: "3rem", marginBottom: "12px" },
  emptyTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", margin: "0 0 8px 0" },
  emptyDesc: { fontSize: "0.9rem", color: "#8888a0", margin: "0 0 20px 0" },
};
