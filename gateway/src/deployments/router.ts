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

  return router;
}
