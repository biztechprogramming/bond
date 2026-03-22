"use client";

import React, { useState } from "react";
import AppDashboard from "./AppDashboard";
import AppDetail from "./AppDetail";
import OneClickShipWizard from "./OneClickShipWizard";
import ShipProgress from "./ShipProgress";
import InfraMap from "../settings/deployment/InfraMap";

const NEW_DEPLOY_UI = process.env.NEXT_PUBLIC_NEW_DEPLOY_UI !== "false";

export type DeployViewMode = "dashboard" | "app-detail" | "new-deploy" | "deploy-progress" | "infrastructure" | "settings";

interface DeploymentPlan {
  id: string;
  repoUrl?: string;
  serverAddress?: string;
  framework?: string;
  buildStrategy?: string;
  environment?: string;
  [key: string]: unknown;
}

export default function DeployPage() {
  const [view, setView] = useState<DeployViewMode>("dashboard");
  const [selectedAppId, setSelectedAppId] = useState<string | null>(null);
  const [activePlan, setActivePlan] = useState<DeploymentPlan | null>(null);

  if (!NEW_DEPLOY_UI) {
    return (
      <div style={s.container}>
        <div style={s.fallback}>
          New deploy UI is disabled. Visit <a href="/settings#deployment" style={{ color: "#6c8aff" }}>Settings &rarr; Deployment</a>.
        </div>
      </div>
    );
  }

  const navigateTo = (mode: DeployViewMode, appId?: string) => {
    if (appId) setSelectedAppId(appId);
    setView(mode);
  };

  const handleWizardComplete = (plan: DeploymentPlan) => {
    setActivePlan(plan);
    setView("deploy-progress");
  };

  const renderContent = () => {
    switch (view) {
      case "dashboard":
        return (
          <AppDashboard
            onSelectApp={(id) => navigateTo("app-detail", id)}
            onNewDeploy={() => navigateTo("new-deploy")}
          />
        );
      case "app-detail":
        return (
          <AppDetail
            appId={selectedAppId!}
            onBack={() => navigateTo("dashboard")}
          />
        );
      case "new-deploy":
        return (
          <OneClickShipWizard
            onComplete={handleWizardComplete}
            onCancel={() => navigateTo("dashboard")}
          />
        );
      case "deploy-progress":
        return (
          <ShipProgress
            plan={activePlan!}
            onDone={() => navigateTo("dashboard")}
            onViewApp={(id) => navigateTo("app-detail", id)}
          />
        );
      case "infrastructure":
        return <InfraMap onAddServer={() => setView("new-deploy")} />;
      case "settings":
        return (
          <div style={s.placeholder}>
            Deploy settings — coming soon. Visit <a href="/settings#deployment" style={{ color: "#6c8aff" }}>Settings &rarr; Deployment</a> for now.
          </div>
        );
      default:
        return null;
    }
  };

  return (
    <div style={s.container}>
      <header style={s.header}>
        <a href="/" style={s.backLink}>&larr; Chat</a>
        <h1 style={s.title}>Deploy</h1>
        <nav style={s.nav}>
          {(["dashboard", "infrastructure", "settings"] as const).map((tab) => (
            <button
              key={tab}
              style={view === tab ? { ...s.navBtn, ...s.navBtnActive } : s.navBtn}
              onClick={() => setView(tab)}
            >
              {tab === "dashboard" ? "Apps" : tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </nav>
      </header>
      <div style={s.content}>{renderContent()}</div>
    </div>
  );
}

const s: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", height: "100vh", maxWidth: "1200px", margin: "0 auto", width: "100%" },
  header: { display: "flex", alignItems: "center", gap: "16px", padding: "16px 24px", borderBottom: "1px solid #1e1e2e" },
  backLink: { color: "#6c8aff", textDecoration: "none", fontSize: "0.9rem" },
  title: { fontSize: "1.5rem", fontWeight: 700, margin: 0 },
  nav: { display: "flex", gap: "4px", marginLeft: "auto" },
  navBtn: {
    background: "none", borderWidth: "1px", borderStyle: "solid", borderColor: "transparent", color: "#8888a0", padding: "6px 14px",
    fontSize: "0.85rem", fontWeight: 500, cursor: "pointer", borderRadius: "6px", transition: "all 0.2s",
  },
  navBtnActive: { color: "#6c8aff", borderColor: "#6c8aff", backgroundColor: "rgba(108,138,255,0.08)" },
  content: { flex: 1, overflowY: "auto", padding: "24px", minHeight: 0 },
  fallback: { padding: "40px", textAlign: "center", color: "#8888a0" },
  placeholder: { padding: "40px", textAlign: "center", color: "#8888a0" },
};
