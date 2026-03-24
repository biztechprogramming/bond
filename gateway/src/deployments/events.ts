/**
 * Deployment Events — emit structured events for every significant deployment action.
 *
 * Events are logged to console and emitted to any registered EventBus instance
 * for delivery through Bond's channel infrastructure.
 *
 * Design Doc 039 §17 — Notifications & Events
 */

import { ulid } from "ulid";

export type DeploymentEventType =
  | "script_promoted"
  | "deployment_started"
  | "deployment_succeeded"
  | "deployment_failed"
  | "rollback_triggered"
  | "health_check_failed"
  | "drift_detected"
  | "bug_ticket_filed"
  | "deployment_window_closing"
  | "manual_intervention_needed"
  | "deployment_lock_stale"
  | "monitoring_alert"
  | "discovery_completed"
  | "discovery_agent_started"
  | "discovery_agent_progress"
  | "discovery_user_question"
  | "discovery_agent_completed";

export interface DeploymentEvent {
  id: string;
  type: "deployment_event";
  event: DeploymentEventType;
  environment: string;
  script_id?: string;
  agent_id?: string;
  receipt_id?: string;
  summary: string;
  details?: Record<string, any>;
  timestamp: string;
}

// Optional EventBus integration — set by calling initDeploymentEvents()
type EmitFn = (event: DeploymentEvent) => void;
let _emitter: EmitFn | null = null;

/**
 * Register an emitter function (e.g., wrapping EventBus.emit).
 * Call this during server startup if you want events delivered to subscribers.
 */
export function initDeploymentEvents(emitter: EmitFn): void {
  _emitter = emitter;
}

/**
 * Emit a deployment event.
 * Always logs to console. Also calls the registered emitter if one is set.
 */
export function emitDeploymentEvent(
  eventType: DeploymentEventType,
  params: {
    environment: string;
    script_id?: string;
    agent_id?: string;
    receipt_id?: string;
    summary: string;
    details?: Record<string, any>;
  },
): DeploymentEvent {
  const event: DeploymentEvent = {
    id: ulid(),
    type: "deployment_event",
    event: eventType,
    environment: params.environment,
    script_id: params.script_id,
    agent_id: params.agent_id,
    receipt_id: params.receipt_id,
    summary: params.summary,
    details: params.details,
    timestamp: new Date().toISOString(),
  };

  // Always log
  console.log(`[deployment-event] ${eventType} | ${params.environment} | ${params.summary}`);

  // Emit to registered handler (EventBus integration)
  if (_emitter) {
    try {
      _emitter(event);
    } catch (err: any) {
      console.warn("[deployment-event] emitter error:", err.message);
    }
  }

  return event;
}

// ── Convenience helpers ────────────────────────────────────────────────────

export function emitDeploymentStarted(env: string, scriptId: string, agentId: string): void {
  emitDeploymentEvent("deployment_started", {
    environment: env,
    script_id: scriptId,
    agent_id: agentId,
    summary: `Deployment of ${scriptId} started in ${env}`,
  });
}

export function emitDeploymentSucceeded(
  env: string, scriptId: string, agentId: string, receiptId: string, durationMs: number,
): void {
  emitDeploymentEvent("deployment_succeeded", {
    environment: env,
    script_id: scriptId,
    agent_id: agentId,
    receipt_id: receiptId,
    summary: `${scriptId} deployed successfully to ${env} (${Math.round(durationMs / 1000)}s)`,
    details: { duration_ms: durationMs },
  });
}

export function emitDeploymentFailed(
  env: string, scriptId: string, agentId: string, receiptId: string, error: string,
): void {
  emitDeploymentEvent("deployment_failed", {
    environment: env,
    script_id: scriptId,
    agent_id: agentId,
    receipt_id: receiptId,
    summary: `${scriptId} failed in ${env}: ${error.slice(0, 200)}`,
    details: { error },
  });
}

export function emitRollbackTriggered(env: string, scriptId: string, agentId: string): void {
  emitDeploymentEvent("rollback_triggered", {
    environment: env,
    script_id: scriptId,
    agent_id: agentId,
    summary: `Rollback triggered for ${scriptId} in ${env}`,
  });
}

export function emitHealthCheckFailed(env: string, agentId: string, details: Record<string, any>): void {
  emitDeploymentEvent("health_check_failed", {
    environment: env,
    agent_id: agentId,
    summary: `Health check failed in ${env}`,
    details,
  });
}

export function emitMonitoringAlert(
  env: string, category: string, component: string, message: string,
): void {
  emitDeploymentEvent("monitoring_alert", {
    environment: env,
    summary: `[${category}] ${component}: ${message.slice(0, 200)}`,
    details: { category, component, message },
  });
}

export function emitDiscoveryCompleted(
  env: string, resourceName: string, layerCount: number,
): void {
  emitDeploymentEvent("discovery_completed", {
    environment: env,
    summary: `Discovery completed for ${resourceName}: ${layerCount} layer(s)`,
    details: { resource_name: resourceName, layer_count: layerCount },
  });
}

export function emitScriptPromoted(env: string, scriptId: string, version: string, promotedBy: string): void {
  emitDeploymentEvent("script_promoted", {
    environment: env,
    script_id: scriptId,
    summary: `${scriptId}@${version} promoted to ${env} by ${promotedBy}`,
    details: { version, promoted_by: promotedBy },
  });

  // Notify the deploy agent so it can begin the autonomous deployment flow
  notifyDeployAgent(env, scriptId, version, promotedBy).catch(() => {});
}

// ── Agent Notification ───────────────────────────────────────────────────────

type AgentNotifyFn = (agentName: string, message: string) => Promise<void>;
let _agentNotifier: AgentNotifyFn | null = null;

/**
 * Register a function that can send a message to a named agent's conversation.
 * Call during server startup after the gateway's messaging layer is ready.
 */
export function initAgentNotifier(notifier: AgentNotifyFn): void {
  _agentNotifier = notifier;
}

/**
 * Send a deployment notification to the deploy agent for the given environment.
 */
export async function notifyDeployAgent(
  envName: string,
  scriptId: string,
  version: string,
  promotedBy: string,
): Promise<void> {
  if (!_agentNotifier) return;
  const agentName = `deploy-${envName}`;
  const message = `Script ${scriptId}@${version} has been promoted to ${envName} by ${promotedBy}. Execute the deployment flow.`;
  try {
    await _agentNotifier(agentName, message);
  } catch (err: any) {
    console.warn(`[deployment-event] Failed to notify ${agentName}:`, err.message);
  }
}

// ── Agent Startup Message ────────────────────────────────────────────────────

/**
 * Returns the message to inject when a deploy agent container comes online.
 */
export function getAgentStartupMessage(env: string): string {
  return `Welcome back. You are deploy-${env}. Check for pending deployments:\n1. Call deploy_action with action "status" to see what needs deploying\n2. Call deploy_action with action "health-check" to verify environment health\n3. Report your findings`;
}
