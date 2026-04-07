"use client";

import React, { useEffect, useState } from "react";
import AgentsTab from "./agents/AgentsTab";
import DeploymentTab from "./deployment/DeploymentTab";
import PromptsTab from "./prompts/PromptsTab";
import ChannelsTab from "./channels/ChannelsTab";
import SkillsTab from "./skills/SkillsTab";
import OptimizationTab from "./optimization/OptimizationTab";
import ImagesTab from "./images/ImagesTab";
import ContainerHostsTab from "./containers/ContainerHostsTab";
import LlmTab from "./llm/LlmTab";
import EmbeddingTab from "./embedding/EmbeddingTab";
import ApiKeysTab from "./apikeys/ApiKeysTab";
import GeneralTab from "./general/GeneralTab";
import { s } from "./styles";

const TABS = [
  { id: "agents", label: "Agents" },
  { id: "containers", label: "Container Hosts" },
  { id: "deployment", label: "Deployment" },
  { id: "channels", label: "Channels" },
  { id: "prompts", label: "Prompts" },
  { id: "images", label: "Images" },
  { id: "llm", label: "LLM" },
  { id: "embedding", label: "Embedding" },
  { id: "api-keys", label: "API Keys" },
  { id: "skills", label: "Skills" },
  { id: "optimization", label: "Optimization" },
  { id: "general", label: "General" },
] as const;

type TabId = (typeof TABS)[number]["id"];

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState<TabId>("agents");

  useEffect(() => {
    const hash = window.location.hash.replace("#", "");
    if (TABS.some((t) => t.id === hash)) setActiveTab(hash as TabId);
  }, []);

  const switchTab = (tab: TabId) => {
    setActiveTab(tab);
    window.history.replaceState(null, "", `#${tab}`);
  };

  return (
    <div style={s.container}>
      <style>{`
        .settings-tab-bar::-webkit-scrollbar { display: none; }
        @media (max-width: 768px) {
          .settings-content-area { padding: 12px !important; gap: 16px !important; }
          .settings-header { padding: 12px 16px !important; }
          .settings-section { padding: 16px !important; }
        }
      `}</style>
      <header className="settings-header" style={s.header}>
        <a href="/" style={s.backLink}>&larr; Chat</a>
        <h1 style={s.title}>Settings</h1>
      </header>

      <div style={s.tabBarWrapper}>
        <div className="settings-tab-bar" style={s.tabBar}>
          {TABS.map((tab) => (
            <button
              key={tab.id}
              style={activeTab === tab.id ? { ...s.tab, ...s.tabActive } : s.tab}
              onClick={() => switchTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>
        <div style={s.tabBarFade} aria-hidden />
      </div>

      <div className="settings-content-area" style={s.content}>
        {activeTab === "agents" && <AgentsTab />}
        {activeTab === "containers" && <ContainerHostsTab />}
        {activeTab === "deployment" && <DeploymentTab />}
        {activeTab === "channels" && <ChannelsTab />}
        {activeTab === "prompts" && <PromptsTab />}
        {activeTab === "images" && <ImagesTab />}
        {activeTab === "llm" && <LlmTab />}
        {activeTab === "embedding" && <EmbeddingTab />}
        {activeTab === "api-keys" && <ApiKeysTab />}
        {activeTab === "skills" && <SkillsTab />}
        {activeTab === "optimization" && <OptimizationTab />}
        {activeTab === "general" && <GeneralTab />}
      </div>
    </div>
  );
}
