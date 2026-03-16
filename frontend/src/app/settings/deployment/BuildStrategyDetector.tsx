import React, { useEffect, useState } from "react";
import { GATEWAY_API } from "@/lib/config";

type BuildStrategy = "auto" | "dockerfile" | "docker-compose" | "script";

interface DetectionResult {
  language: string;
  framework: string;
  detected_file: string;
  suggested_build_cmd: string;
  suggested_start_cmd: string;
}

interface Props {
  repoUrl: string;
  strategy: BuildStrategy;
  onDetected: (result: DetectionResult) => void;
}

export default function BuildStrategyDetector({ repoUrl, strategy, onDetected }: Props) {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DetectionResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (strategy !== "auto" || !repoUrl) {
      setResult(null);
      setError("");
      return;
    }

    let cancelled = false;
    const detect = async () => {
      setLoading(true);
      setError("");
      setResult(null);
      try {
        const res = await fetch(`${GATEWAY_API}/deployments/detect-build`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ repo_url: repoUrl, branch: "main" }),
        });
        if (!res.ok) {
          if (res.status === 404) {
            setError("Build detection API not available yet. Select a strategy manually.");
          } else {
            setError("Detection failed. Select a strategy manually.");
          }
          return;
        }
        const data: DetectionResult = await res.json();
        if (!cancelled) {
          setResult(data);
          onDetected(data);
        }
      } catch {
        if (!cancelled) {
          setError("Could not reach detection API. Select a strategy manually.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    const timer = setTimeout(detect, 500);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [repoUrl, strategy, onDetected]);

  if (strategy !== "auto") return null;

  return (
    <div style={styles.container}>
      {loading && (
        <div style={styles.detecting}>Scanning repository...</div>
      )}
      {result && (
        <div style={styles.detected}>
          Detected: <strong style={{ color: "#e0e0e8" }}>{result.language}</strong>
          {result.framework && <> ({result.framework})</>}
          {result.detected_file && (
            <span style={styles.file}> — {result.detected_file} found</span>
          )}
        </div>
      )}
      {error && (
        <div style={styles.error}>{error}</div>
      )}
      {!loading && !result && !error && repoUrl && (
        <div style={styles.hint}>Enter a repository URL to auto-detect build strategy</div>
      )}
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { marginTop: "8px" },
  detecting: { fontSize: "0.82rem", color: "#6c8aff", fontStyle: "italic" },
  detected: { fontSize: "0.82rem", color: "#6cffa0" },
  file: { color: "#8888a0" },
  error: { fontSize: "0.82rem", color: "#ffcc44" },
  hint: { fontSize: "0.82rem", color: "#5a5a6e" },
};
