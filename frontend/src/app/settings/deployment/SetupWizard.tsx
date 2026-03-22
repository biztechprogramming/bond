import React, { useState } from "react";
import { callReducer } from "@/hooks/useSpacetimeDB";
import { getConnection } from "@/lib/spacetimedb-client";
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

const DEFAULT_PROMPT_TEMPLATE = (env: string, displayName?: string, resourceDescriptions?: string) => {
  const name = displayName || env;
  const resources = resourceDescriptions || "(no resource descriptions configured)";
  return `You are deploy-${env}, the autonomous deployment agent for the **${name}** environment.

## Identity
- Agent name: deploy-${env}
- Environment: ${env} (${name})
- Role: Fully autonomous deployment executor — you act without waiting to be told.

## Autonomous Deployment Flow

When a script is promoted to your environment (you will receive a message), execute the full deployment pipeline autonomously:

1. **Gather info** — Call \`deploy_action\` with action "status" to see what is pending.
2. **Load context** — Read the script source from your workspace mounts. Understand what it does.
3. **Validate** — Check that the script is well-formed and targets the correct environment.
4. **Dry-run** — Call \`deploy_action\` with action "dry-run" and the script id. Review the output for warnings.
5. **Pre-hook** — Call \`deploy_action\` with action "pre-hook" if the script defines one.
6. **Deploy** — Call \`deploy_action\` with action "execute" to run the actual deployment.
7. **Post-hook** — Call \`deploy_action\` with action "post-hook" if the script defines one.
8. **Health-check** — Call \`deploy_action\` with action "health-check" to verify the environment is healthy.

Report the outcome after each deployment (success or failure).

## Failure Handling

If any step fails:
1. **Rollback** — Call \`deploy_action\` with action "rollback" to revert the deployment.
2. **Diagnose** — Read relevant source code from your workspace mounts to understand the failure.
3. **Bug ticket** — File a detailed bug ticket with: what failed, the error output, your diagnosis, and a suggested fix.
4. **Report** — Send a failure report summarizing the issue and rollback status.

Never leave a failed deployment in an unknown state. Always rollback before reporting.

## Proactive Monitoring

Between deployments:
- Periodically call \`deploy_action\` with action "health-check" to verify environment health.
- If a health check fails, investigate and report. Do NOT attempt to fix infrastructure yourself.
- Watch for drift by comparing expected state against actual state.
- Report any anomalies immediately.

## User Interaction Patterns

- **Status requests**: Respond with current environment state, pending deployments, and recent activity.
- **Manual deploy requests**: Remind the user that scripts must be promoted through the UI first. You execute what is promoted.
- **Troubleshooting**: Read code, check logs, and provide diagnosis. You have read-only workspace access.
- **Rollback requests**: Execute rollback if a deployment is in a failed state.

## Constraints

- You CANNOT modify code. All workspace mounts are read-only.
- You CANNOT promote scripts. Only users can promote via the UI.
- You CANNOT access secrets directly. The broker injects them during execution.
- You CANNOT deploy scripts not promoted to your environment.
- You CANNOT skip steps in the deployment flow (no jumping straight to execute).
- You MUST rollback on failure before doing anything else.

## Resources
${resources}`;
};

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

    // Save shared settings via SpacetimeDB
    const conn = getConnection();
    if (!conn) {
      setMsg("Error: No SpacetimeDB connection");
      setCreating(false);
      return;
    }

    const settingsToSave = [
      { key: "deployment.shared.model", value: shared.model },
      { key: "deployment.shared.utility_model", value: shared.utility_model },
      { key: "deployment.shared.sandbox_image", value: shared.sandbox_image },
    ];
    for (const s of settingsToSave) {
      conn.reducers.setSetting({ key: s.key, value: s.value, keyType: "string" });
    }

    const errors: string[] = [];
    for (const env of environments) {
      try {
        const agentId = `deploy-${env.name}`;
        const displayName = personalNames[env.name] || env.display_name;

        conn.reducers.addAgent({
          id: agentId,
          name: `deploy-${env.name}`,
          displayName,
          systemPrompt: DEFAULT_PROMPT_TEMPLATE(env.name, displayName),
          model: shared.model,
          utilityModel: shared.utility_model,
          tools: "",
          sandboxImage: shared.sandbox_image || "",
          maxIterations: 200,
          isActive: true,
          isDefault: false,
        });

        // Add channel
        conn.reducers.addAgentChannel({
          id: `${agentId}-webchat`,
          agentId,
          channel: "webchat",
          sandboxOverride: "",
          enabled: true,
        });

        // Add workspace mounts
        for (const m of roMounts) {
          conn.reducers.addAgentMount({
            id: `${agentId}-${m.mount_name}`,
            agentId,
            hostPath: m.host_path,
            mountName: m.mount_name,
            containerPath: m.container_path,
            readonly: true,
          });
        }
      } catch {
        errors.push(`${env.name}: reducer error`);
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
    borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e",
  },
  envSlug: { fontSize: "0.75rem", color: "#8888a0", marginBottom: "8px", fontFamily: "monospace" },
  input: {
    width: "100%",
    backgroundColor: "#1e1e2e",
    borderWidth: "1px", borderStyle: "solid", borderColor: "#2a2a3e",
    borderRadius: "8px",
    padding: "8px 10px",
    color: "#e0e0e8",
    fontSize: "0.9rem",
    outline: "none",
    boxSizing: "border-box" as const,
  },
  envHint: { fontSize: "0.72rem", color: "#5a5a6e", marginTop: "4px" },
  mountInfo: { fontSize: "0.82rem", color: "#8888a0", backgroundColor: "#12121a", borderRadius: "8px", padding: "12px 16px", borderWidth: "1px", borderStyle: "solid", borderColor: "#1e1e2e" },
  mountLine: { fontSize: "0.8rem", color: "#e0e0e8", marginTop: "4px", fontFamily: "monospace" },
  msg: { fontSize: "0.85rem" },
  button: {
    backgroundColor: "#6c8aff",
    color: "#fff",
    borderWidth: 0, borderStyle: "none", borderColor: "transparent",
    borderRadius: "8px",
    padding: "12px 24px",
    fontSize: "0.95rem",
    fontWeight: 600,
    cursor: "pointer",
    alignSelf: "flex-start",
  },
};
