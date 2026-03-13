import React from "react";
import AgentCard from "./AgentCard";

interface Agent {
  id: string;
  name: string;
  display_name: string;
  model: string;
  utility_model: string;
  is_active: boolean;
}

interface Props {
  agents: Agent[];
  environments: { name: string; display_name: string }[];
  sharedModel: string;
  sharedUtilityModel: string;
  sharedSandboxImage: string;
  onEditAgent: (agent: Agent) => void;
  onEditAll: () => void;
}

export default function AgentCardGrid({
  agents,
  environments,
  sharedModel,
  sharedUtilityModel,
  sharedSandboxImage,
  onEditAgent,
  onEditAll,
}: Props) {
  // Order agents by environment order
  const orderedAgents = environments
    .map((env) => agents.find((a) => a.name === `deploy-${env.name}`))
    .filter(Boolean) as Agent[];

  const shortModel = (m: string) => m.split("/").pop() || m;

  return (
    <div>
      <div style={styles.header}>
        <h2 style={styles.title}>Deployment Agents</h2>
        <button style={styles.editAllBtn} onClick={onEditAll}>Edit All</button>
      </div>

      <div style={styles.sharedLine}>
        Shared: {shortModel(sharedModel)} &middot; {shortModel(sharedUtilityModel)}
        {sharedSandboxImage ? ` \u00b7 ${sharedSandboxImage}` : ""}
      </div>

      <div style={styles.grid}>
        {orderedAgents.map((agent, i) => {
          const env = environments.find((e) => agent.name === `deploy-${e.name}`);
          return (
            <React.Fragment key={agent.id}>
              {i > 0 && <span style={styles.arrow}>&rarr;</span>}
              <AgentCard
                agent={agent}
                envLabel={env?.display_name || agent.name.replace("deploy-", "")}
                sharedModel={sharedModel}
                sharedUtilityModel={sharedUtilityModel}
                onClick={() => onEditAgent(agent)}
              />
            </React.Fragment>
          );
        })}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  header: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  editAllBtn: {
    backgroundColor: "#2a2a3e",
    color: "#6c8aff",
    border: "1px solid #3a3a4e",
    borderRadius: "8px",
    padding: "8px 16px",
    fontSize: "0.85rem",
    cursor: "pointer",
  },
  sharedLine: { fontSize: "0.82rem", color: "#8888a0", marginBottom: "16px" },
  grid: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
    overflowX: "auto" as const,
    paddingBottom: "8px",
  },
  arrow: { color: "#5a5a6e", fontSize: "1.2rem", flexShrink: 0 },
};
