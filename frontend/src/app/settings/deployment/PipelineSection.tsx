import React, { useEffect, useState } from "react";
import { BACKEND_API } from "@/lib/config";
import PipelineRow from "./PipelineRow";
import { DeployStatus } from "./StatusIndicator";

interface Promotion {
  script_name: string;
  version: string;
  environments: { environment: string; status: DeployStatus }[];
}

interface Props {
  environmentNames: string[];
}

export default function PipelineSection({ environmentNames }: Props) {
  const [promotions, setPromotions] = useState<Promotion[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${BACKEND_API}/deployments/promotions`);
        if (res.ok) {
          setPromotions(await res.json());
        }
      } catch {
        // API may not exist yet
      }
      setLoaded(true);
    })();
  }, []);

  if (!loaded) return null;

  return (
    <div style={styles.section}>
      <h3 style={styles.title}>Pipelines</h3>
      {promotions.length === 0 ? (
        <p style={styles.empty}>No deployment pipelines yet. Promote scripts from the deployment broker to see them here.</p>
      ) : (
        promotions.map((p) => (
          <PipelineRow
            key={`${p.script_name}-${p.version}`}
            scriptName={p.script_name}
            version={p.version}
            environments={p.environments}
          />
        ))
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  section: { borderTop: "1px solid #1e1e2e", marginTop: "24px", paddingTop: "20px" },
  title: { fontSize: "0.95rem", fontWeight: 600, color: "#8888a0", margin: "0 0 12px 0" },
  empty: { fontSize: "0.85rem", color: "#5a5a6e" },
};
