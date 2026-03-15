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

  // Resources
  router.use("/resources", createResourceRouter(config));

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
