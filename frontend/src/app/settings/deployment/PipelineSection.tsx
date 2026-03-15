import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";
import PipelineRow from "./PipelineRow";
import StatusIndicator, { DeployStatus } from "./StatusIndicator";
import PipelineStepView, { PipelineStep } from "./PipelineStepView";
import PipelineRunHistory from "./PipelineRunHistory";
import PipelineYamlEditor from "./PipelineYamlEditor";

interface Promotion {
  script_name: string;
  version: string;
  environments: { environment: string; status: DeployStatus }[];
}

interface PipelineInfo {
  name: string;
  repo?: string;
  trigger?: string;
  last_run_ago?: string;
  last_run_duration?: string;
  steps?: PipelineStep[];
  yaml?: string;
}

interface Props {
  environmentNames: string[];
}

export default function PipelineSection({ environmentNames }: Props) {
  const [promotions, setPromotions] = useState<Promotion[]>([]);
  const [pipeline, setPipeline] = useState<PipelineInfo | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showYamlEditor, setShowYamlEditor] = useState(false);

  useEffect(() => {
    (async () => {
      // Fetch promotions
      try {
        const res = await fetch(`${GATEWAY_API}/deployments/promotions`);
        if (res.ok) {
          setPromotions(await res.json());
        }
      } catch {
        // API may not exist yet
      }

      // Fetch pipeline info (Tier 2)
      try {
        const res = await fetch(`${GATEWAY_API}/deployments/pipeline`);
        if (res.ok) {
          setPipeline(await res.json());
        }
      } catch {
        // API may not exist yet
      }

      setLoaded(true);
    })();
  }, []);

  if (!loaded) return null;

  const hasPipeline = pipeline != null;
  const hasPromotions = promotions.length > 0;

  return (
    <div style={styles.section}>
      <div style={styles.headerRow}>
        <h3 style={styles.title}>Pipelines</h3>
        <button
          style={styles.yamlToggle}
          onClick={() => setShowYamlEditor(!showYamlEditor)}
        >
          {showYamlEditor ? "Hide YAML Editor" : "Edit Pipeline YAML"}
        </button>
      </div>

      {hasPipeline && (
        <div style={styles.pipelineCard}>
          <div style={styles.pipelineHeader}>
            <span style={styles.pipelineName}>{pipeline.name}</span>
            {pipeline.repo && (
              <span style={styles.pipelineRepo}>{pipeline.repo}</span>
            )}
          </div>

          {(pipeline.trigger || pipeline.last_run_ago || pipeline.last_run_duration) && (
            <div style={styles.triggerLine}>
              {pipeline.trigger && <span>Trigger: {pipeline.trigger}</span>}
              {pipeline.last_run_ago && <span> · Last run: {pipeline.last_run_ago}</span>}
              {pipeline.last_run_duration && <span> · Duration: {pipeline.last_run_duration}</span>}
            </div>
          )}

          {pipeline.steps && pipeline.steps.length > 0 && (
            <div style={styles.stepsSection}>
              <span style={styles.subLabel}>Steps:</span>
              <PipelineStepView steps={pipeline.steps} />
            </div>
          )}

          {hasPromotions && (
            <div style={styles.promotionSection}>
              <span style={styles.subLabel}>Environments:</span>
              {promotions.map((p) => (
                <PipelineRow
                  key={`${p.script_name}-${p.version}`}
                  scriptName={p.script_name}
                  version={p.version}
                  environments={p.environments}
                />
              ))}
            </div>
          )}

          <PipelineRunHistory />
        </div>
      )}

      {!hasPipeline && hasPromotions && (
        <>
          {promotions.map((p) => (
            <PipelineRow
              key={`${p.script_name}-${p.version}`}
              scriptName={p.script_name}
              version={p.version}
              environments={p.environments}
            />
          ))}
          <PipelineRunHistory />
        </>
      )}

      {!hasPipeline && !hasPromotions && (
        <p style={styles.empty}>
          No deployment pipelines yet. Add a <code style={styles.code}>.bond/deploy.yml</code> to
          your repository or promote scripts from the deployment broker to see them here.
        </p>
      )}

      {showYamlEditor && (
        <div style={styles.yamlSection}>
          <PipelineYamlEditor
            initialYaml={pipeline?.yaml || ""}
            repoUrl={pipeline?.repo}
          />
        </div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  section: { borderTop: "1px solid #1e1e2e", marginTop: "24px", paddingTop: "20px" },
  headerRow: { display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "12px" },
  title: { fontSize: "0.95rem", fontWeight: 600, color: "#8888a0", margin: 0 },
  yamlToggle: {
    backgroundColor: "#2a2a3e",
    color: "#e0e0e8",
    border: "1px solid #3a3a4e",
    borderRadius: "8px",
    padding: "6px 12px",
    fontSize: "0.8rem",
    cursor: "pointer",
  },
  pipelineCard: {
    backgroundColor: "#12121a",
    border: "1px solid #1e1e2e",
    borderRadius: "12px",
    padding: "16px",
    display: "flex",
    flexDirection: "column",
    gap: "14px",
  },
  pipelineHeader: { display: "flex", alignItems: "center", gap: "10px" },
  pipelineName: { fontSize: "1rem", fontWeight: 600, color: "#e0e0e8" },
  pipelineRepo: { fontSize: "0.75rem", color: "#6c8aff", fontFamily: "monospace" },
  triggerLine: { fontSize: "0.8rem", color: "#8888a0" },
  stepsSection: { display: "flex", flexDirection: "column", gap: "8px" },
  promotionSection: { display: "flex", flexDirection: "column", gap: "6px" },
  subLabel: { fontSize: "0.75rem", fontWeight: 600, color: "#8888a0" },
  empty: { fontSize: "0.85rem", color: "#5a5a6e" },
  code: {
    backgroundColor: "#0a0a12",
    padding: "2px 6px",
    borderRadius: "4px",
    fontSize: "0.8rem",
    fontFamily: "monospace",
    color: "#6c8aff",
  },
  yamlSection: { marginTop: "16px", borderTop: "1px solid #1e1e2e", paddingTop: "16px" },
};
