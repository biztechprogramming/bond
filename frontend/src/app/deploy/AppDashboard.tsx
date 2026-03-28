"use client";

import React, { useMemo } from "react";
import { useComponents, useEnvironments, useResources, useAlerts, useSpacetimeDB } from "@/hooks/useSpacetimeDB";
import { getComponentResources, type ComponentResourceRow } from "@/lib/spacetimedb-client";
import AppCard, { type AppInfo } from "./AppCard";
import DeploymentRunsList from "./DeploymentRunsList";
import type { DeploymentRun } from "./DeploymentRunsList";
import { GATEWAY_API } from "@/lib/config";

interface Props {
  onSelectApp: (id: string) => void;
  onNewDeploy: () => void;
}

/** Fetch all component_resources for a set of component IDs. */
function useAllComponentResources(componentIds: string[]): Map<string, ComponentResourceRow[]> {
  return useSpacetimeDB(() => {
    const map = new Map<string, ComponentResourceRow[]>();
    for (const cid of componentIds) {
      const crs = getComponentResources(cid);
      if (crs.length > 0) map.set(cid, crs);
    }
    return map;
  }, [componentIds.join(",")]);
}

export default function AppDashboard({ onSelectApp, onNewDeploy }: Props) {
  const components = useComponents();
  const environments = useEnvironments();
  const alerts = useAlerts();

  const componentIds = useMemo(() => components.map((c) => c.id), [components]);
  const crMap = useAllComponentResources(componentIds);

  const apps: AppInfo[] = useMemo(() => {
    const envLookup = new Map(environments.map((e) => [e.name, e]));

    return components
      .filter((c) => c.isActive)
      .map((comp) => {
        const crs = crMap.get(comp.id) || [];
        const compAlerts = alerts.filter((a) => a.componentId === comp.id);
        const envStatuses = crs.map((cr) => {
          const env = envLookup.get(cr.environment);
          return {
            name: env?.displayName || cr.environment,
            status: (cr.healthCheck ? "healthy" : "inactive") as "healthy" | "inactive",
          };
        });

        const hasError = compAlerts.some((a) => a.severity === "critical" || a.severity === "error");
        const hasWarning = compAlerts.some((a) => a.severity === "warning");
        const healthStatus: AppInfo["healthStatus"] = hasError ? "down" : hasWarning ? "degraded" : crs.length > 0 ? "healthy" : "unknown";

        return {
          id: comp.id,
          name: comp.name,
          displayName: comp.displayName,
          componentType: comp.componentType,
          framework: comp.framework || "Unknown",
          runtime: comp.runtime || "",
          repositoryUrl: comp.repositoryUrl || "",
          environments: envStatuses,
          healthStatus,
          alertCount: compAlerts.length,
        };
      });
  }, [components, crMap, alerts, environments]);

  const handleRollback = (run: DeploymentRun) => {
    fetch(`${GATEWAY_API}/deployments/rollback`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        script_id: run.script_id,
        environment: run.environment,
        target_version: run.script_version,
      }),
    }).catch(() => {});
  };

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

      {/* Recent Deployments */}
      <div style={s.recentSection}>
        <h3 style={s.recentHeading}>Recent Deployments</h3>
        <DeploymentRunsList
          limit={10}
          onRollback={handleRollback}
          onViewLogs={(run) => onSelectApp(run.script_id)}
        />
      </div>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  toolbar: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "20px" },
  heading: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", margin: 0 },
  deployBtn: {
    backgroundColor: "#6c8aff", color: "#fff", borderWidth: 0, borderStyle: "none", borderColor: "transparent", borderRadius: "8px",
    padding: "10px 20px", fontSize: "0.9rem", fontWeight: 600, cursor: "pointer",
  },
  grid: {
    display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: "16px",
  },
  empty: { textAlign: "center", padding: "60px 20px" },
  emptyIcon: { fontSize: "3rem", marginBottom: "12px" },
  emptyTitle: { fontSize: "1.1rem", fontWeight: 600, color: "#e0e0e8", margin: "0 0 8px 0" },
  emptyDesc: { fontSize: "0.9rem", color: "#8888a0", margin: "0 0 20px 0" },
  recentSection: { marginTop: "32px", paddingTop: "24px", borderTopWidth: "1px", borderTopStyle: "solid", borderTopColor: "#1e1e2e" },
  recentHeading: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8", margin: "0 0 12px 0" },
};
