/**
 * Deployment Router — mounts all deployment sub-routers.
 *
 * Routes:
 *   /api/v1/deployments/environments    → environment CRUD (user-auth)
 *   /api/v1/deployments/pipeline        → pipeline view
 *   /api/v1/deployments/promote         → promotion + approval (user-auth)
 *   /api/v1/deployments/promotions      → promotion state queries
 *   /api/v1/deployments/scripts         → script registry
 *   /api/v1/deployments/receipts        → receipt access
 *   /api/v1/deployments/agents          → deployment agent controls (pause/resume/abort)
 *   /api/v1/deployments/session         → session token issue (Phase 1 helper)
 *   /api/v1/deployments/allocations     → environment port/directory allocations (§077)
 */

import { Router } from "express";
import path from "node:path";
import fs from "node:fs";
import { homedir } from "node:os";
import type { GatewayConfig } from "../config/index.js";
import { createEnvironmentsRouter } from "./environments.js";
import { createPromotionRouter } from "./promotion.js";
import { createScriptsRouter } from "./scripts-router.js";
import { createReceiptsRouter } from "./receipts-router.js";
import { issueSessionToken, extractUserIdentity } from "./session-tokens.js";
import { seedDefaultEnvironments } from "./stdb.js";
import { getQueue, removeFromQueue } from "./queue.js";
import { getHealthStatus } from "./health-scheduler.js";
import { loadSecrets, encryptSecrets } from "./secrets.js";
import { listLogDates, readLog } from "./log-stream.js";
import { getEnvironmentHistory } from "./stdb.js";
import { handleQuickDeploy } from "./quick-deploy.js";
import { detectBuildStrategy } from "./build-detector.js";
import { createResourceRouter } from "./resource-router.js";
import {
  getTriggers, createTrigger, deleteTrigger,
  disableTrigger, enableTrigger, handleWebhookPush,
} from "./trigger-handler.js";
import { SCRIPT_TEMPLATES } from "./script-templates.js";
import { createPipelineRouter } from "./pipeline-router.js";
import { listManifests, readManifest } from "./manifest.js";
import { addDiscoveryListener } from "./events.js";
import { runAgentDiscovery } from "./discovery.js";
import { runProbes } from "./discovery-agent.js";
import type { SshExecParams } from "./discovery-tools.js";
import { buildDiscoveryPrompt, mapAgentEventToDiscovery } from "./discovery-sse-adapter.js";
import { getResource } from "./resources.js";
import { ulid } from "ulid";
import { getMonitoringAlerts } from "./stdb.js";
import { createSecretsRouter } from "./secrets-router.js";
import { createAlertRulesRouter } from "./alert-rules-router.js";
import { createCompareRouter } from "./compare-router.js";
import { createComponentsRouter } from "./components-router.js";
import { createFolderBrowserRouter } from "./folder-browser.js";
import { createAllocationRouter } from "./allocation-router.js";
import { collectLogs } from "./log-stream.js";
import { createRun, getRun, listRuns, updateRunStatus, getRunEmitter, executeDeploymentRun } from "./runs.js";
import { BackendClient } from "../backend/client.js";
import { generateDeploymentPlan } from "./deployment-planner.js";
import { executeDeploymentPlan as execPlan } from "./deployment-executor.js";
import type { DeploymentPlan } from "./deployment-planner.js";
import type { DiscoveryState } from "./discovery-agent.js";

export const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export function createDeploymentsRouter(config: GatewayConfig): Router {
  const router = Router();
  const backendClient = new BackendClient(config.backendUrl);

  // Ensure deployments directory structure exists
  for (const dir of [
    path.join(DEPLOYMENTS_DIR, "scripts", "registry"),
    path.join(DEPLOYMENTS_DIR, "hooks"),
    path.join(DEPLOYMENTS_DIR, "health"),
    path.join(DEPLOYMENTS_DIR, "secrets"),
    path.join(DEPLOYMENTS_DIR, "receipts"),
    path.join(DEPLOYMENTS_DIR, "locks"),
    path.join(DEPLOYMENTS_DIR, "logs"),
    path.join(DEPLOYMENTS_DIR, "discovery", "manifests"),
    path.join(DEPLOYMENTS_DIR, "discovery", "scripts"),
    path.join(DEPLOYMENTS_DIR, "discovery", "proposals"),
  ]) {
    fs.mkdirSync(dir, { recursive: true });
  }

  // Seed default environments on startup (non-blocking)
  seedDefaultEnvironments(config).catch((err) => {
    console.warn("[deployments] Seed failed:", err.message);
  });

  // Environment management
  router.use("/environments", createEnvironmentsRouter(config));

  // Promotion + pipeline
  const promotionRouter = createPromotionRouter(config);
  router.use("/", promotionRouter); // mounts /pipeline, /promote, /promotions

  // Script registry
  router.use("/scripts", createScriptsRouter(config));

  // Receipts
  router.use("/receipts", createReceiptsRouter(config));

  // Pipeline-as-Code
  const pipelineCodeRouter = createPipelineRouter();
  router.use("/pipeline-code", pipelineCodeRouter);

  // Alias: frontend calls /deployments/validate-yaml
  router.post("/validate-yaml", (req: any, res: any, next: any) => {
    req.url = "/validate";
    pipelineCodeRouter(req, res, next);
  });

  // Resources
  router.use("/resources", createResourceRouter(config));

  // Secrets management (§8.1)
  router.use("/secrets", createSecretsRouter(config));

  // Alert rules (§8.2)
  router.use("/alert-rules", createAlertRulesRouter(config));

  // Environment comparison (§8.3)
  router.use("/compare", createCompareRouter(config));

  // Components (§045a)
  router.use("/components", createComponentsRouter(config));

  // Folder browser (§044)
  router.use("/browse", createFolderBrowserRouter(config));

  // Environment allocations (§077)
  router.use("/allocations", createAllocationRouter(config));

  // Session token issue — Phase 1 helper for testing
  // In production this would be behind proper auth
  router.post("/session", (req: any, res: any) => {
    const { user_id = "user", role = "owner" } = req.body || {};
    const token = issueSessionToken(user_id, role);
    res.json({ token, user_id, role });
  });

  // Agent controls — pause/resume/abort
  router.post("/agents/:agentId/pause", (req: any, res: any) => {
    const { agentId } = req.params;
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    // Write pause flag to a file
    const flagPath = path.join(DEPLOYMENTS_DIR, "locks", `${agentId}.pause`);
    fs.writeFileSync(flagPath, JSON.stringify({ paused_at: new Date().toISOString(), by: identity.user_id }));
    res.json({ success: true, message: `Agent ${agentId} paused` });
  });

  router.post("/agents/:agentId/resume", (req: any, res: any) => {
    const { agentId } = req.params;
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    const flagPath = path.join(DEPLOYMENTS_DIR, "locks", `${agentId}.pause`);
    if (fs.existsSync(flagPath)) fs.unlinkSync(flagPath);
    res.json({ success: true, message: `Agent ${agentId} resumed` });
  });

  router.post("/agents/:agentId/abort", (req: any, res: any) => {
    const { agentId } = req.params;
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    // Write abort flag
    const flagPath = path.join(DEPLOYMENTS_DIR, "locks", `${agentId}.abort`);
    fs.writeFileSync(flagPath, JSON.stringify({ aborted_at: new Date().toISOString(), by: identity.user_id }));
    res.json({ success: true, message: `Abort signal sent to ${agentId}` });
  });

  // Queue endpoints
  router.get("/queue/:env", (_req: any, res: any) => {
    const { env } = _req.params;
    const queue = getQueue(env);
    res.json({ environment: env, queue, length: queue.length });
  });

  router.delete("/queue/:env/:scriptId/:version", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    const { env, scriptId, version } = req.params;
    const removed = removeFromQueue(env, scriptId, version);
    if (removed) {
      res.json({ success: true, message: `Removed ${scriptId}@${version} from ${env} queue` });
    } else {
      res.status(404).json({ error: `${scriptId}@${version} not found in ${env} queue` });
    }
  });

  // Health status endpoint
  router.get("/health/:env", (_req: any, res: any) => {
    const { env } = _req.params;
    const status = getHealthStatus(env);
    if (status) {
      res.json(status);
    } else {
      res.json({ environment: env, status: "unknown", message: "No health check data available yet" });
    }
  });

  // Secrets encryption endpoint
  router.post("/secrets/:env/encrypt", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    const { env } = req.params;
    try {
      const secrets = loadSecrets(env);
      if (Object.keys(secrets).length === 0) {
        return res.status(404).json({ error: `No secrets found for environment '${env}'` });
      }
      encryptSecrets(env, secrets);
      res.json({ success: true, message: `Secrets for '${env}' encrypted`, keys: Object.keys(secrets).length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Log streaming endpoints
  router.get("/logs/:env", (_req: any, res: any) => {
    const { env } = _req.params;
    const dates = listLogDates(env);
    res.json({ environment: env, dates });
  });

  router.get("/logs/:env/:date", (_req: any, res: any) => {
    const { env, date } = _req.params;
    const offset = parseInt(_req.query.offset as string || "0", 10);
    const result = readLog(env, date, offset);
    if (!result) {
      return res.status(404).json({ error: `No log found for ${env} on ${date}` });
    }
    res.json({ environment: env, date, ...result });
  });

  // Live log collection trigger (§8.4)
  router.post("/logs/:env/collect", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    const { env } = req.params;
    try {
      const result = collectLogs(env);
      res.json({ environment: env, ...result, message: "Log collection triggered" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Environment history endpoint
  router.get("/environments/:name/history", async (req: any, res: any) => {
    const { name } = req.params;
    const limit = parseInt(req.query.limit as string || "50", 10);
    try {
      const history = await getEnvironmentHistory(config, name, limit);
      res.json(history);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Quick Deploy endpoint
  router.post("/quick-deploy", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const result = await handleQuickDeploy(req.body, DEPLOYMENTS_DIR, config, identity.user_id);
      res.json(result);
    } catch (err: any) {
      console.error("[quick-deploy] failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // --- Deployment Runs ---
  const userAuth = (req: any, res: any, next: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    (req as any).userId = identity.user_id;
    next();
  };

  // POST /runs — start a deployment run
  router.post("/runs", userAuth, async (req: any, res: any) => {
    const { script_id, script_version, environment, resource_id, run_type, plan } = req.body;
    const userId = (req as any).userId || "anonymous";
    const run = createRun({
      script_id: script_id || "quick-deploy",
      script_version: script_version || "1",
      environment: environment || "dev",
      resource_id,
      triggered_by: userId,
      run_type: run_type || "deploy",
      plan,
    });
    // Start execution in background
    executeDeploymentRun(run, DEPLOYMENTS_DIR, config).catch(() => {});
    res.json(run);
  });

  // GET /runs — list runs
  router.get("/runs", async (_req: any, res: any) => {
    const env = _req.query.environment as string | undefined;
    res.json(listRuns(env));
  });

  // GET /runs/:id — get run status
  router.get("/runs/:id", async (req: any, res: any) => {
    const run = getRun(req.params.id);
    if (!run) return res.status(404).json({ error: "Run not found" });
    res.json(run);
  });

  // GET /runs/:id/stream — SSE stream of run output
  router.get("/runs/:id/stream", (req: any, res: any) => {
    const run = getRun(req.params.id);
    if (!run) return res.status(404).json({ error: "Run not found" });

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    });

    // Send current state
    res.write(`data: ${JSON.stringify({ type: "init", run })}\n\n`);

    const emitter = getRunEmitter(run.id);
    if (!emitter) {
      res.write(`data: ${JSON.stringify({ type: "done", status: run.status })}\n\n`);
      res.end();
      return;
    }

    const onStep = (data: any) => res.write(`data: ${JSON.stringify({ type: "step", ...data })}\n\n`);
    const onLog = (data: any) => res.write(`data: ${JSON.stringify({ type: "log", ...data })}\n\n`);
    const onStatus = (data: any) => res.write(`data: ${JSON.stringify({ type: "status", ...data })}\n\n`);
    const onDone = (data: any) => {
      res.write(`data: ${JSON.stringify({ type: "done", ...data })}\n\n`);
      cleanup();
      res.end();
    };

    emitter.on("step", onStep);
    emitter.on("log", onLog);
    emitter.on("status", onStatus);
    emitter.on("done", onDone);

    const heartbeat = setInterval(() => {
      res.write(`: heartbeat\n\n`);
    }, 15000);

    const cleanup = () => {
      clearInterval(heartbeat);
      emitter.off("step", onStep);
      emitter.off("log", onLog);
      emitter.off("status", onStatus);
      emitter.off("done", onDone);
    };

    req.on("close", cleanup);
  });

  // POST /runs/:id/cancel — cancel a run
  router.post("/runs/:id/cancel", userAuth, async (req: any, res: any) => {
    const run = getRun(req.params.id);
    if (!run) return res.status(404).json({ error: "Run not found" });
    if (run.status !== "queued" && run.status !== "running") {
      return res.status(400).json({ error: "Run is not active" });
    }
    updateRunStatus(run.id, "cancelled");
    res.json({ ok: true });
  });

  // POST /rollback — initiate rollback
  router.post("/rollback", userAuth, async (req: any, res: any) => {
    const { receipt_id, environment } = req.body;
    const userId = (req as any).userId || "anonymous";
    const run = createRun({
      script_id: receipt_id || "rollback",
      script_version: "1",
      environment: environment || "dev",
      triggered_by: userId,
      run_type: "rollback",
    });
    executeDeploymentRun(run, DEPLOYMENTS_DIR, config).catch(() => {});
    res.json(run);
  });

  // POST /execute-plan — SSE stream deployment from a plan (legacy endpoint for ShipProgress)
  router.post("/execute-plan", userAuth, async (req: any, res: any) => {
    const plan = req.body;
    const userId = (req as any).userId || "anonymous";
    const run = createRun({
      script_id: plan?.appName || "quick-deploy",
      script_version: "1",
      environment: plan?.environment || "dev",
      triggered_by: userId,
      run_type: "deploy",
      plan,
    });

    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    });

    const emitter = getRunEmitter(run.id);
    if (emitter) {
      emitter.on("step", (data: any) => res.write(`data: ${JSON.stringify(data)}\n\n`));
      emitter.on("log", (data: any) => res.write(`data: ${JSON.stringify({ ...data, type: "log" })}\n\n`));
      emitter.on("done", (data: any) => {
        res.write(`data: ${JSON.stringify({ ...data, completed: data.status === "success", error: data.status === "failed" })}\n\n`);
        res.end();
      });
    }

    // Start execution
    executeDeploymentRun(run, DEPLOYMENTS_DIR, config).catch(() => {});
  });

  // Build detection endpoint
  router.post("/detect-build", async (req: any, res: any) => {
    const { repo_url, branch = "main" } = req.body || {};
    if (!repo_url) return res.status(400).json({ error: "repo_url is required" });

    try {
      const result = await detectBuildStrategy(repo_url, branch);
      res.json(result);
    } catch (err: any) {
      console.error("[detect-build] failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // Script templates
  router.get("/script-templates", (_req: any, res: any) => {
    res.json(SCRIPT_TEMPLATES);
  });

  // Trigger management
  router.get("/triggers", async (_req: any, res: any) => {
    try {
      const triggers = await getTriggers(config);
      res.json(triggers);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.post("/triggers", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      const { script_id, repo_url, branch, tag_pattern, environment, cron_schedule, enabled } = req.body;
      if (!script_id || !repo_url || !branch || !environment) {
        return res.status(400).json({ error: "script_id, repo_url, branch, and environment are required" });
      }
      const id = await createTrigger(config, {
        script_id, repo_url, branch,
        tag_pattern: tag_pattern || undefined,
        environment,
        cron_schedule: cron_schedule || undefined,
        enabled: enabled !== false,
      });
      res.json({ id, success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.delete("/triggers/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      await deleteTrigger(config, req.params.id);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.put("/triggers/:id/disable", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      await disableTrigger(config, req.params.id);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.put("/triggers/:id/enable", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      await enableTrigger(config, req.params.id);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // Discovery SSE session buffers — buffer events between POST and GET (§072)
  const discoveryBuffers = new Map<string, { events: any[]; listeners: Set<(event: any) => void>; cleanup: () => void }>();

  const DISCOVERY_EVENT_TYPES = new Set([
    "discovery_agent_started",
    "discovery_agent_progress",
    "discovery_user_question",
    "discovery_agent_completed",
  ]);

  // POST /agent-discover — agent-first deployment discovery (§080)
  // Accepts agent_id + repo_id (new) or resource_id (legacy fallback)
  router.post("/agent-discover", async (req: any, res: any) => {
    const body = req.body || {};
    const agentId = body.agent_id;
    const repoId = body.repo_id;
    const resourceId = body.resource_id;
    const env = body.environment || "dev";
    const sessionId = ulid();

    // Agent-first path: agent_id + repo_id required (§080)
    // Legacy fallback: resource_id only (§072)
    if (!agentId && !resourceId) {
      return res.status(400).json({ status: "error", reason: "agent_id and repo_id are required (or resource_id for legacy mode)" });
    }

    // Resolve connection info from resource if provided
    let conn: any = {};
    let resourceName: string | undefined;
    if (resourceId) {
      const resource = await getResource(config, resourceId);
      if (!resource) {
        return res.status(404).json({ status: "error", reason: "Resource not found" });
      }
      // Environment isolation: resource must belong to the requested environment (§080, preserves discovery.ts line 57)
      if (resource.environment && resource.environment !== env) {
        return res.status(403).json({
          status: "denied",
          action: "discover",
          reason: `Resource '${resource.name}' belongs to environment '${resource.environment}', not '${env}'. Agents can only discover resources in their own environment.`,
        });
      }
      try { conn = JSON.parse(resource?.connection_json || "{}"); } catch {}
      resourceName = resource?.name;
    }

    const repoPath = conn.repo_path || body.repo_path;
    const repoUrl = conn.repo_url || body.repo_url;

    // Build SSH params if server info available
    const sshParams: SshExecParams | undefined = conn.host ? {
      host: conn.host,
      port: conn.port || 22,
      user: conn.user || "deploy",
      key_path: conn.key_path,
      command: "",
      parse_as: "raw",
    } : undefined;

    // If agent_id is provided, use the agent-first path (§080)
    if (agentId) {
      // Register SSE buffer
      const session = { events: [] as any[], listeners: new Set<(event: any) => void>(), cleanup: () => {} };
      discoveryBuffers.set(sessionId, session);

      // Helper to push event to buffer + notify listeners
      const emit = (payload: any) => {
        session.events.push(payload);
        for (const fn of session.listeners) { try { fn(payload); } catch {} }
      };

      // Run probes + agent turn in background
      (async () => {
        try {
          // Emit started event immediately so frontend transitions from "connecting"
          emit({
            event: "discovery_agent_started",
            session_id: sessionId,
            mode: "full",
          });

          // Phase 1: Pre-gather probes (fast, no LLM)
          const probeResults = await runProbes(repoPath, sshParams);

          // Emit probe progress
          for (const probe of probeResults.probes_run) {
            if (probe.success && probe.fields_discovered.length > 0) {
              emit({
                event: "discovery_agent_progress",
                session_id: sessionId,
                field: probe.fields_discovered[0],
                value: (probeResults as any)[probe.fields_discovered[0]],
                confidence: { source: "detected", detail: `From ${probe.tool}`, score: 0.8 },
                completeness: { ready: false, required_coverage: 0, recommended_coverage: 0, missing_required: [], low_confidence: [] },
                probe_name: probe.tool,
              });
            }
          }

          // Phase 2: Create conversation and send agent message
          const conversationId = ulid();
          await backendClient.createConversation(conversationId, agentId, "discovery", `Discovery: ${resourceName || repoId || "repo"}`);

          const discoveryPrompt = buildDiscoveryPrompt(repoId || repoPath || repoUrl || "unknown", probeResults, resourceId);

          // Stream agent turn and map events to discovery format
          const accumulatedText = { value: "" };
          for await (const sseEvent of backendClient.conversationTurnStream(conversationId, discoveryPrompt, agentId)) {
            console.log(`[agent-discover] SSE event received: event=${sseEvent.event}, data_keys=${Object.keys(sseEvent.data).join(",")}`);
            const mapped = mapAgentEventToDiscovery(sseEvent, sessionId, accumulatedText);
            if (mapped) {
              const events = Array.isArray(mapped) ? mapped : [mapped];
              for (const ev of events) {
              console.log(`[agent-discover] Emitting mapped event: ${ev.event}, msg_type=${(ev as any).msg_type || "n/a"}`);
              emit(ev);

              // On completion, cache state and auto-generate plan
              if (ev.event === "discovery_agent_completed" && ev.state) {
                const state = ev.state as DiscoveryState;
                discoveryStateCache.set(sessionId, state);
                if (state.completeness?.ready) {
                  generateDeploymentPlan(state, backendClient).then((plan) => {
                    planStore.set(plan.id, plan);
                    emit({ ...ev, event: "discovery_agent_completed", session_id: sessionId, plan });
                  }).catch((err) => {
                    console.error("[auto-plan] failed to generate plan:", err.message);
                  });
                }
                setTimeout(() => { discoveryBuffers.delete(sessionId); }, 30_000);
              }
              }
            }
          }

          // If stream ended without a "done" event, send completion
          const hasDone = session.events.some((e: any) => e.event === "discovery_agent_completed");
          if (!hasDone) {
            const finalPayload = mapAgentEventToDiscovery({ event: "done", data: {} }, sessionId, accumulatedText);
            if (finalPayload) {
              const finals = Array.isArray(finalPayload) ? finalPayload : [finalPayload];
              for (const ev of finals) emit(ev);
            }
          }
        } catch (err: any) {
          console.error("[agent-discover] agent-first discovery failed:", err.message);
          emit({
            event: "discovery_agent_completed",
            session_id: sessionId,
            state: null,
            completeness: null,
            error: err.message,
          });
        }
      })();

      return res.json({ status: "ok", action: "discover", environment: env, session_id: sessionId, mode: "agent-first", conversation_id: `pending:${sessionId}` });
    }

    // Legacy fallback: resource_id-only path (§072)
    const session = { events: [] as any[], listeners: new Set<(event: any) => void>(), cleanup: () => {} };
    const cleanupListener = addDiscoveryListener((event: any) => {
      if (!DISCOVERY_EVENT_TYPES.has(event.event) || event.details?.session_id !== sessionId) return;
      const payload: any = { event: event.event, ...event.details, session_id: sessionId };
      session.events.push(payload);
      for (const fn of session.listeners) {
        try { fn(payload); } catch {}
      }
      if (event.event === "discovery_agent_completed" && event.details?.state) {
        const state = event.details.state as DiscoveryState;
        discoveryStateCache.set(sessionId, state);
        if (state.completeness?.ready) {
          generateDeploymentPlan(state, backendClient).then((plan) => {
            planStore.set(plan.id, plan);
            const planPayload = { event: "discovery_agent_completed", session_id: sessionId, plan, ...event.details };
            for (const fn of session.listeners) { try { fn(planPayload); } catch {} }
          }).catch((err) => {
            console.error("[auto-plan] failed to generate plan:", err.message);
          });
        }
        setTimeout(() => { discoveryBuffers.delete(sessionId); cleanupListener(); }, 30_000);
      }
    });
    session.cleanup = cleanupListener;
    discoveryBuffers.set(sessionId, session);

    const agentParams = {
      source: resourceName,
      repoPath,
      repoUrl,
      serverHost: conn.host,
      serverPort: conn.port,
      sshUser: conn.user,
      sshKeyPath: conn.key_path,
      env,
      sessionId,
      backendClient,
    };
    discoveryParamsCache.set(sessionId, agentParams);
    setTimeout(() => { discoveryParamsCache.delete(sessionId); }, 300_000);

    runAgentDiscovery(agentParams).catch((err) => {
      console.error("[agent-discover] legacy discovery failed:", err.message);
      const { emitDeploymentEvent: emit } = require("./events.js");
      emit("discovery_agent_completed", {
        environment: env,
        summary: `Discovery failed: ${err.message}`,
        details: { state: null, completeness: null, error: err.message, session_id: sessionId },
      });
    });

    res.json({ status: "ok", action: "discover", environment: env, session_id: sessionId, mode: "legacy" });
  });


  // Discovery agent SSE stream (§072)
  router.get("/discovery/stream/:sessionId", (req: any, res: any) => {
    const { sessionId } = req.params;
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    const session = discoveryBuffers.get(sessionId);
    let closed = false;

    // Declare onEvent BEFORE cleanupStream to avoid TDZ
    const onEvent = (payload: any) => { write(payload); };

    function cleanupStream() {
      if (session) session.listeners.delete(onEvent);
    }

    const write = (payload: any) => {
      if (closed) return;
      console.log(`[discovery-stream] SSE event sent for session=${sessionId}: event=${payload.event}, field=${payload.field || "N/A"}`);
      res.write(`data: ${JSON.stringify(payload)}\n\n`);
      if (payload.event === "discovery_agent_completed") {
        res.end();
        closed = true;
        cleanupStream();
      }
    };

    // Replay buffered events
    if (session) {
      for (const evt of session.events) {
        write(evt);
      }
    }

    // Listen for future events
    if (session) {
      session.listeners.add(onEvent);
    }


    // Fallback: if no buffer exists, listen globally (shouldn't happen in normal flow)
    let cleanupGlobal: (() => void) | undefined;
    if (!session) {
      cleanupGlobal = addDiscoveryListener((event: any) => {
        if (!DISCOVERY_EVENT_TYPES.has(event.event) || event.details?.session_id !== sessionId) return;
        write({ event: event.event, ...event.details, session_id: sessionId });
      });
    }

    req.on("close", () => {
      closed = true;
      cleanupStream();
      if (cleanupGlobal) cleanupGlobal();
    });
  });

  // Discovery agent answer endpoint (§072)
  // Track discovery params per session so we can resume after answers
  const discoveryParamsCache = new Map<string, any>();

  router.post("/discovery/answer/:sessionId", async (req: any, res: any) => {
    const { field, value } = req.body || {};
    const { sessionId } = req.params;
    if (!field || value === undefined) {
      return res.status(400).json({ error: "field and value are required" });
    }
    // Store the answer
    const answersDir = path.join(DEPLOYMENTS_DIR, "discovery", "answers");
    fs.mkdirSync(answersDir, { recursive: true });
    const answerFile = path.join(answersDir, `${sessionId}.json`);
    let answers: Record<string, string> = {};
    if (fs.existsSync(answerFile)) {
      try { answers = JSON.parse(fs.readFileSync(answerFile, "utf8")); } catch {}
    }
    answers[field] = value;
    fs.writeFileSync(answerFile, JSON.stringify(answers, null, 2));

    // Emit a progress event for the user-provided answer so the SSE stream picks it up
    const { emitDeploymentEvent: emit } = await import("./events.js");
    emit("discovery_agent_progress", {
      environment: "dev",
      summary: `User provided: ${field}`,
      details: {
        field,
        value,
        confidence: { source: "user-provided", detail: "Provided by user", score: 1.0 },
        completeness: null,
        probe_name: "user_answer",
        session_id: sessionId,
      },
    });

    // Re-run discovery with the cached params + user answers to reach completion
    const cachedParams = discoveryParamsCache.get(sessionId);
    if (cachedParams) {
      // Run a follow-up discovery pass in the background — it will emit completion
      runAgentDiscovery({
        ...cachedParams,
        sessionId,
      }).catch((err) => {
        console.error("[discovery/answer] follow-up discovery failed:", err.message);
      });
    }

    res.json({ success: true, field, value });
  });

  // Discovery agent cancel endpoint (§072)
  router.post("/discovery/cancel/:sessionId", (req: any, res: any) => {
    const { sessionId } = req.params;
    const flagPath = path.join(DEPLOYMENTS_DIR, "locks", `discovery-${sessionId}.abort`);
    fs.writeFileSync(flagPath, JSON.stringify({ cancelled_at: new Date().toISOString() }));
    res.json({ success: true, message: `Discovery ${sessionId} cancelled` });
  });

  // Discovery manifests
  router.get("/discovery/manifests", (_req: any, res: any) => {
    const manifests = listManifests();
    res.json({ manifests });
  });

  router.get("/discovery/manifests/:name", (_req: any, res: any) => {
    const { name } = _req.params;
    const manifest = readManifest(name);
    if (!manifest) return res.status(404).json({ error: `Manifest '${name}' not found` });
    res.json(manifest);
  });

  // Discovery proposals
  router.get("/discovery/proposals/:app", (_req: any, res: any) => {
    const { app } = _req.params;
    const proposalDir = path.join(DEPLOYMENTS_DIR, "discovery", "proposals", app);
    if (!fs.existsSync(proposalDir)) return res.json({ app, levels: [] });
    const levels = fs.readdirSync(proposalDir).filter(d => fs.statSync(path.join(proposalDir, d)).isDirectory());
    const result: Record<string, string[]> = {};
    for (const level of levels) {
      result[level] = fs.readdirSync(path.join(proposalDir, level));
    }
    res.json({ app, levels: result });
  });

  // ── Deployment Plan endpoints ─────────────────────────────────────────────

  const planStore = new Map<string, DeploymentPlan>();
  const discoveryStateCache = new Map<string, DiscoveryState>();

  // POST /plan/generate — generate a deployment plan from discovery state
  router.post("/plan/generate", async (req: any, res: any) => {
    const { session_id, discovery_state } = req.body || {};
    const state: DiscoveryState | undefined = discovery_state || discoveryStateCache.get(session_id);
    if (!state) {
      return res.status(400).json({ error: "discovery_state or valid session_id is required" });
    }
    try {
      const plan = await generateDeploymentPlan(state, backendClient);
      planStore.set(plan.id, plan);
      res.json(plan);
    } catch (err: any) {
      console.error("[plan/generate] failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // POST /plan/:planId/approve — approve and execute a plan
  router.post("/plan/:planId/approve", userAuth, async (req: any, res: any) => {
    const { planId } = req.params;
    const { ssh_config } = req.body || {};
    const plan = planStore.get(planId);
    if (!plan) return res.status(404).json({ error: "Plan not found" });
    if (!ssh_config?.host || !ssh_config?.username) {
      return res.status(400).json({ error: "ssh_config with host and username is required" });
    }

    const userId = (req as any).userId || "anonymous";
    const run = createRun({
      script_id: plan.app_name,
      script_version: "1",
      environment: "deploy",
      triggered_by: userId,
      run_type: "deploy",
      plan: plan as any,
    });

    // Execute in background
    const emitter = getRunEmitter(run.id);
    execPlan(plan, ssh_config, {
      onStepStart: (step) => emitter?.emit("step", { step_id: step.id, action: step.action, description: step.description, status: "running" }),
      onStepComplete: (step, result) => emitter?.emit("step", { step_id: step.id, status: result.status, output: result.output, error: result.error }),
      onLog: (stepId, line) => emitter?.emit("log", { step_id: stepId, line }),
    }).then((result) => {
      updateRunStatus(run.id, result.status === "success" ? "success" : "failed");
      emitter?.emit("done", { status: result.status, steps: result.steps });
    }).catch((err) => {
      updateRunStatus(run.id, "failed");
      emitter?.emit("done", { status: "failed", error: err.message });
    });

    res.json({ run_id: run.id, plan_id: planId });
  });

  // GET /plan/:planId/stream — SSE stream for plan execution (reuse run stream)
  router.get("/plan/:planId/stream", (req: any, res: any) => {
    // Find the run associated with this plan
    const { planId } = req.params;
    const allRuns = listRuns();
    const run = allRuns.find(r => (r.plan as any)?.id === planId);
    if (!run) return res.status(404).json({ error: "No execution found for this plan" });

    // Redirect to run stream logic
    req.params.id = run.id;
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "Access-Control-Allow-Origin": "*",
    });

    res.write(`data: ${JSON.stringify({ type: "init", run })}\n\n`);
    const emitter = getRunEmitter(run.id);
    if (!emitter) {
      res.write(`data: ${JSON.stringify({ type: "done", status: run.status })}\n\n`);
      res.end();
      return;
    }

    const onStep = (data: any) => res.write(`data: ${JSON.stringify({ type: "step", ...data })}\n\n`);
    const onLog = (data: any) => res.write(`data: ${JSON.stringify({ type: "log", ...data })}\n\n`);
    const onDone = (data: any) => { res.write(`data: ${JSON.stringify({ type: "done", ...data })}\n\n`); cleanup(); res.end(); };
    emitter.on("step", onStep);
    emitter.on("log", onLog);
    emitter.on("done", onDone);

    const heartbeat = setInterval(() => res.write(`: heartbeat\n\n`), 15000);
    const cleanup = () => { clearInterval(heartbeat); emitter.off("step", onStep); emitter.off("log", onLog); emitter.off("done", onDone); };
    req.on("close", cleanup);
  });

  // POST /plan/:planId/rollback — execute rollback steps
  router.post("/plan/:planId/rollback", userAuth, async (req: any, res: any) => {
    const { planId } = req.params;
    const { ssh_config } = req.body || {};
    const plan = planStore.get(planId);
    if (!plan) return res.status(404).json({ error: "Plan not found" });
    if (!plan.rollback_steps.length) return res.status(400).json({ error: "No rollback steps in plan" });
    if (!ssh_config?.host || !ssh_config?.username) {
      return res.status(400).json({ error: "ssh_config with host and username is required" });
    }

    const userId = (req as any).userId || "anonymous";
    const rollbackPlan: DeploymentPlan = { ...plan, id: plan.id + "-rollback", steps: plan.rollback_steps, rollback_steps: [] };
    const run = createRun({
      script_id: plan.app_name,
      script_version: "1",
      environment: "deploy",
      triggered_by: userId,
      run_type: "rollback",
      plan: rollbackPlan as any,
    });

    execPlan(rollbackPlan, ssh_config, {
      onLog: (_stepId, line) => getRunEmitter(run.id)?.emit("log", { line }),
    }).then((result) => {
      updateRunStatus(run.id, result.status === "success" ? "success" : "failed");
      getRunEmitter(run.id)?.emit("done", { status: result.status });
    }).catch((err) => {
      updateRunStatus(run.id, "failed");
      getRunEmitter(run.id)?.emit("done", { status: "failed", error: err.message });
    });

    res.json({ run_id: run.id, plan_id: planId });
  });

  // Monitoring status
  router.get("/monitoring/:env", async (req: any, res: any) => {
    const { env } = req.params;
    try {
      const alerts = await getMonitoringAlerts(config, env, 20);
      const health = getHealthStatus(env);
      res.json({ environment: env, health, recent_alerts: alerts });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.put("/monitoring/:env", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    const { env } = req.params;
    // Monitoring config is stored per environment — return acknowledgment
    res.json({ success: true, environment: env, config: req.body });
  });

  // Webhook receiver — no user auth (receives GitHub payloads)
  router.post("/webhook/push", async (req: any, res: any) => {
    try {
      const payload = req.body;
      if (!payload?.repository?.clone_url || !payload?.ref) {
        return res.status(400).json({ error: "Invalid webhook payload" });
      }
      const result = await handleWebhookPush(config, payload);
      res.json(result);
    } catch (err: any) {
      console.error("[webhook] push handler failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
