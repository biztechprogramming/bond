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
import { getResource } from "./resources.js";
import { ulid } from "ulid";
import { getMonitoringAlerts } from "./stdb.js";
import { createSecretsRouter } from "./secrets-router.js";
import { createAlertRulesRouter } from "./alert-rules-router.js";
import { createCompareRouter } from "./compare-router.js";
import { createComponentsRouter } from "./components-router.js";
import { createFolderBrowserRouter } from "./folder-browser.js";
import { collectLogs } from "./log-stream.js";

export const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export function createDeploymentsRouter(config: GatewayConfig): Router {
  const router = Router();

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

  // POST /agent-discover — frontend-facing agent discovery initiation (§072)
  router.post("/agent-discover", async (req: any, res: any) => {
    const body = req.body || {};
    const resourceId = body.resource_id;
    if (!resourceId) {
      return res.status(400).json({ status: "error", reason: "resource_id is required" });
    }



    const env = body.environment || "dev";
    const sessionId = ulid();
    const resource = await getResource(config, resourceId);
    let conn: any = {};
    try { conn = JSON.parse(resource?.connection_json || "{}"); } catch {}

    runAgentDiscovery({
      source: resource?.name,
      repoPath: conn.repo_path || body.repo_path,
      repoUrl: conn.repo_url || body.repo_url,
      serverHost: conn.host,
      serverPort: conn.port,
      sshUser: conn.user,
      sshKeyPath: conn.key_path,
      env,
      sessionId,
    }).catch((err) => {
      console.error("[agent-discover] agent discovery failed:", err.message);
    });

    res.json({ status: "ok", action: "discover", environment: env, session_id: sessionId });
  });

  // Discovery agent SSE stream (§072)
  router.get("/discovery/stream/:sessionId", (req: any, res: any) => {
    const { sessionId } = req.params;
    res.setHeader("Content-Type", "text/event-stream");
    res.setHeader("Cache-Control", "no-cache");
    res.setHeader("Connection", "keep-alive");
    res.flushHeaders();

    const eventTypes = new Set([
      "discovery_agent_started",
      "discovery_agent_progress",
      "discovery_user_question",
      "discovery_agent_completed",
    ]);

    let closed = false;
    const cleanup = addDiscoveryListener((event: any) => {
      if (closed) return;
      if (eventTypes.has(event.event) && event.details?.session_id === sessionId) {
        const payload: any = { event: event.event, ...event.details };
        payload.session_id = sessionId;
        res.write(`data: ${JSON.stringify(payload)}\n\n`);

        if (event.event === "discovery_agent_completed") {
          res.end();
          closed = true;
          cleanup();
        }
      }
    });

    req.on("close", () => {
      closed = true;
      cleanup();
    });
  });

  // Discovery agent answer endpoint (§072)
  router.post("/discovery/answer/:sessionId", (req: any, res: any) => {
    const { field, value } = req.body || {};
    if (!field || value === undefined) {
      return res.status(400).json({ error: "field and value are required" });
    }
    // Store the answer — the agent will pick it up on next iteration
    const answersDir = path.join(DEPLOYMENTS_DIR, "discovery", "answers");
    fs.mkdirSync(answersDir, { recursive: true });
    const answerFile = path.join(answersDir, `${req.params.sessionId}.json`);
    let answers: Record<string, string> = {};
    if (fs.existsSync(answerFile)) {
      try { answers = JSON.parse(fs.readFileSync(answerFile, "utf8")); } catch {}
    }
    answers[field] = value;
    fs.writeFileSync(answerFile, JSON.stringify(answers, null, 2));
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
