import React, { useState } from "react";
import { BACKEND_API } from "@/lib/config";
import SharedSettingsForm from "./SharedSettingsForm";

interface Environment {
  name: string;
  display_name: string;
}

interface WorkspaceMount {
  host_path: string;
  mount_name: string;
  container_path: string;
  readonly: boolean;
}

interface Props {
  environments: Environment[];
  availableModels: { id: string; name: string }[];
  sandboxImages: string[];
  existingMounts: WorkspaceMount[];
  onCreated: () => void;
}

const DEFAULT_PROMPT_TEMPLATE = (env: string) => `You are a deployment agent for the ${env} environment.

Your role:
- Execute deployment scripts that have been promoted to your environment
- Run health checks and monitor environment state
- File detailed bug tickets when deployments fail
- Read code for troubleshooting (you have read-only access)

Your constraints:
- You CANNOT modify code. All workspace mounts are read-only.
- You CANNOT promote scripts. Only users can promote via the UI.
- You CANNOT access secrets directly. The broker injects them during execution.
- You CANNOT deploy scripts not promoted to your environment.

When a deployment fails:
1. Review the error output from the broker
2. Read relevant source code from your workspace mounts
3. File a detailed bug ticket with diagnosis and suggested fix
4. Report the failure to the user

Environment: ${env}`;

export default function SetupWizard({ environments, availableModels, sandboxImages, existingMounts, onCreated }: Props) {
  const [shared, setShared] = useState({
    model: availableModels.length > 0 ? availableModels[0].id : "anthropic/claude-sonnet-4-20250514",
    utility_model: availableModels.length > 0 ? availableModels[0].id : "anthropic/claude-sonnet-4-20250514",
    sandbox_image: "",
  });

  const [personalNames, setPersonalNames] = useState<Record<string, string>>(() => {
    const names: Record<string, string> = {};
    environments.forEach((env) => { names[env.name] = ""; });
    return names;
  });

  const [creating, setCreating] = useState(false);
  const [msg, setMsg] = useState("");

  const roMounts = existingMounts.map((m) => ({
    host_path: m.host_path,
    mount_name: m.mount_name,
    container_path: m.container_path,
    readonly: true,
  }));

  const createAll = async () => {
    setCreating(true);
    setMsg("");

    // Save shared settings first
    try {
      const settingsToSave = [
        { key: "deployment.shared.model", value: shared.model },
        { key: "deployment.shared.utility_model", value: shared.utility_model },
        { key: "deployment.shared.sandbox_image", value: shared.sandbox_image },
      ];
      for (const s of settingsToSave) {
        await fetch(`${BACKEND_API}/settings/${s.key}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: s.value }),
        });
      }
    } catch {
      // Settings API might not support this yet, continue anyway
    }

    const errors: string[] = [];
    for (const env of environments) {
      try {
        const body = {
          name: `deploy-${env.name}`,
          display_name: personalNames[env.name] || env.display_name,
          system_prompt: DEFAULT_PROMPT_TEMPLATE(env.name),
          model: shared.model,
          utility_model: shared.utility_model,
          sandbox_image: shared.sandbox_image || null,
          workspace_mounts: roMounts,
          channels: [{ channel: "webchat", enabled: true, sandbox_override: null }],
        };

        const res = await fetch(`${BACKEND_API}/agents`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });

        if (!res.ok) {
          const data = await res.json();
          errors.push(`${env.name}: ${data.detail || "failed"}`);
        }
      } catch {
        errors.push(`${env.name}: network error`);
      }
    }

    if (errors.length > 0) {
      setMsg(`Created with errors: ${errors.join("; ")}`);
    } else {
      setMsg("All agents created successfully.");
    }
    setCreating(false);
    onCreated();
  };

  return (
    <div style={styles.container}>
      <h2 style={styles.title}>Set Up Deployment Agents</h2>
      <p style={styles.subtitle}>
        Create deployment agents for all environments in one step. Each agent gets a unique slug
        (deploy-env) and shares the same model configuration.
      </p>

      <SharedSettingsForm
        settings={shared}
        onChange={setShared}
        availableModels={availableModels}
        sandboxImages={sandboxImages}
        onSave={() => {}}
      />

      <h3 style={styles.sectionTitle}>Environments</h3>
      <div style={styles.envGrid}>
        {environments.map((env) => (
          <div key={env.name} style={styles.envCard}>
            <div style={styles.envSlug}>deploy-{env.name}</div>
            <input
              style={styles.input}
              value={personalNames[env.name]}
              onChange={(e) => setPersonalNames({ ...personalNames, [env.name]: e.target.value })}
              placeholder={env.display_name}
            />
            <div style={styles.envHint}>Display name</div>
          </div>
        ))}
      </div>

      {roMounts.length > 0 && (
        <div style={styles.mountInfo}>
          <strong style={{ color: "#e0e0e8" }}>Workspace Mounts</strong> (all read-only):
          {roMounts.map((m, i) => (
            <div key={i} style={styles.mountLine}>{m.host_path} &rarr; {m.container_path} (RO)</div>
          ))}
        </div>
      )}

      {msg && <div style={{ ...styles.msg, color: msg.includes("error") ? "#ff6c8a" : "#6cffa0" }}>{msg}</div>}

      <button style={{ ...styles.button, opacity: creating ? 0.5 : 1 }} onClick={createAll} disabled={creating}>
        {creating ? "Creating..." : "Create All Agents"}
      </button>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  container: { display: "flex", flexDirection: "column", gap: "16px" },
  title: { fontSize: "1.1rem", fontWeight: 600, color: "#6c8aff", margin: 0 },
  subtitle: { fontSize: "0.85rem", color: "#8888a0", margin: 0 },
  sectionTitle: { fontSize: "0.95rem", fontWeight: 600, color: "#e0e0e8", margin: "8px 0 0 0" },
  envGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
    gap: "12px",
  },
  envCard: {
    backgroundColor: "#12121a",
    borderRadius: "12px",
    padding: "16px",
    border: "1px solid #1e1e2e",
  },
  envSlug: { fontSize: "0.75rem", color: "#8888a0", marginBottom: "8px", fontFamily: "monospace" },
  input: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    border: "1px solid #2a2a3e",
    borderRadius: "8px",
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.9rem",
    outline: "none",
    boxSizing: "border-box" as const,
  },
  envHint: { fontSize: "0.72rem", color: "#5a5a6e", marginTop: "4px" },
  mountInfo: { fontSize: "0.82rem", color: "#8888a0", backgroundColor: "#12121a", borderRadius: "8px", padding: "12px 16px", border: "1px solid #1e1e2e" },
  mountLine: { fontSize: "0.8rem", color: "#e0e0e8", marginTop: "4px", fontFamily: "monospace" },
  msg: { fontSize: "0.85rem" },
  button: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    border: "none",
    borderRadius: "8px",
    padding: "12px 24px",
    fontSize: "0.95rem",
    fontWeight: 600,
    cursor: "pointer",
    alignSelf: "flex-start",
  },
};
