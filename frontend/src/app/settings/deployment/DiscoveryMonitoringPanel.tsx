import React, { useState } from "react";
import MonitoringSection from "./MonitoringSection";
import MonitoringConfig from "./MonitoringConfig";
import DiscoveryView from "./DiscoveryView";
import ProposalViewer from "./ProposalViewer";
import TopologyGraph from "./TopologyGraph";
import IssueTracker from "./IssueTracker";

interface Props {
  environment: string;
  resources: any[];
}

type Tab = "discovery" | "monitoring" | "issues";

export default function DiscoveryMonitoringPanel({ environment, resources }: Props) {
  const [tab, setTab] = useState<Tab>("discovery");
  const [selectedResource, setSelectedResource] = useState<string | null>(null);
  const [showProposals, setShowProposals] = useState<string | null>(null);
  const [showConfig, setShowConfig] = useState(false);
  const [monitorKey, setMonitorKey] = useState(0);

  // Check for topology data in resources
  const topologyResource = resources.find((r) => {
    try {
      const state = typeof r.state_json === "string" ? JSON.parse(r.state_json || "{}") : (r.state_json || {});
      return state.topology;
    } catch { return false; }
  });

  const topology = topologyResource ? (() => {
    try {
      const state = typeof topologyResource.state_json === "string" ? JSON.parse(topologyResource.state_json) : topologyResource.state_json;
      return state.topology;
    } catch { return null; }
  })() : null;

  if (showProposals) {
    return <ProposalViewer appName={showProposals} onBack={() => setShowProposals(null)} />;
  }

  if (selectedResource) {
    return (
      <div style={styles.container}>
        <DiscoveryView resourceName={selectedResource} onBack={() => setSelectedResource(null)} />
        <button style={styles.secondaryButton} onClick={() => setShowProposals(selectedResource)}>
          View Proposals
        </button>
      </div>
    );
  }

  return (
    <div style={styles.container}>
      {/* Tab bar */}
      <div style={styles.tabRow}>
        {([["discovery", "Discovery"], ["monitoring", "Monitoring"], ["issues", "Issues"]] as [Tab, string][]).map(([key, label]) => (
          <button
            key={key}
            style={tab === key ? styles.activeTab : styles.tab}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Discovery */}
      {tab === "discovery" && (
        <div style={{ display: "flex", flexDirection: "column" as const, gap: 12 }}>
          {topology && (
            <div style={styles.card}>
              <span style={styles.cardTitle}>Topology</span>
              <TopologyGraph topology={topology} />
            </div>
          )}

          <div style={styles.card}>
            <span style={styles.cardTitle}>Resources</span>
            {resources.length === 0 ? (
              <span style={{ fontSize: "0.8rem", color: "#8888a0" }}>No resources in this environment.</span>
            ) : (
              resources.map((r) => (
                <div key={r.id || r.name} style={styles.resourceRow}>
                  <div style={{ flex: 1 }}>
                    <div style={{ fontSize: "0.85rem", color: "#e0e0e8" }}>{r.display_name || r.name}</div>
                    <div style={{ fontSize: "0.7rem", color: "#8888a0" }}>{r.resource_type}</div>
                  </div>
                  <button style={styles.smallButton} onClick={() => setSelectedResource(r.name)}>
                    Discover
                  </button>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {/* Monitoring */}
      {tab === "monitoring" && (
        <div style={{ display: "flex", flexDirection: "column" as const, gap: 12 }}>
          <MonitoringSection key={monitorKey} environment={environment} />
          <div style={{ display: "flex", gap: 8 }}>
            <button
              style={styles.secondaryButton}
              onClick={() => setShowConfig(!showConfig)}
            >
              {showConfig ? "Hide Config" : "Configure"}
            </button>
          </div>
          {showConfig && (
            <MonitoringConfig
              environment={environment}
              onSave={() => { setShowConfig(false); setMonitorKey((k) => k + 1); }}
            />
          )}
        </div>
      )}

      {/* Issues */}
      {tab === "issues" && (
        <IssueTracker environment={environment} />
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: 12 },
  tabRow: { display: "flex", gap: 4 },
  tab: {
    backgroundColor: "#12121a",
    color: "#8888a0",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
    borderRadius: 6,
    padding: "6px 14px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  activeTab: {
    backgroundColor: "#2a2a4a",
    color: "#e0e0e8",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#6c8aff",
    borderRadius: 6,
    padding: "6px 14px",
    fontSize: "0.8rem",
    cursor: "pointer",
    fontWeight: 600,
  },
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
  resourceRow: {
    display: "flex",
    alignItems: "center",
    gap: 12,
    padding: "8px 0",
    borderBottomWidth: "1px", borderBottomStyle: "solid", borderBottomColor: "#1e1e2e",
  },
  smallButton: {
    backgroundColor: "#2a4a2a",
    color: "#6cffa0",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#3a5a3a",
    borderRadius: 6,
    padding: "4px 10px",
    fontSize: "0.75rem",
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
