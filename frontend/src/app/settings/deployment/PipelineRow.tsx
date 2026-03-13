import React from "react";
import StatusIndicator, { DeployStatus } from "./StatusIndicator";

interface EnvStatus {
  environment: string;
  status: DeployStatus;
}

interface Props {
  scriptName: string;
  version: string;
  environments: EnvStatus[];
}

export default function PipelineRow({ scriptName, version, environments }: Props) {
  return (
    <div style={styles.row}>
      <div style={styles.name}>
        {scriptName} <span style={styles.version}>({version})</span>
      </div>
      <div style={styles.envLine}>
        {environments.map((env, i) => (
          <React.Fragment key={env.environment}>
            <StatusIndicator status={env.status} showLabel={false} />
            <span style={styles.envLabel}>{env.environment}</span>
            {i < environments.length - 1 && <span style={styles.arrow}>&rarr;</span>}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  row: { padding: "12px 0", borderBottom: "1px solid #1e1e2e" },
  name: { fontSize: "0.9rem", color: "#e0e0e8", fontWeight: 500, marginBottom: "6px" },
  version: { fontSize: "0.8rem", color: "#8888a0", fontWeight: 400 },
  envLine: { display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" as const },
  envLabel: { fontSize: "0.75rem", color: "#8888a0", textTransform: "uppercase" as const },
  arrow: { color: "#5a5a6e", fontSize: "0.85rem" },
};
