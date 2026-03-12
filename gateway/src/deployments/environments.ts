/**
 * Deployment Environment Management API.
 *
 * All endpoints require user-session auth — agent broker tokens are rejected.
 * Writes go to SpacetimeDB via reducers; reads via SQL.
 */

import { Router } from "express";
import { ulid } from "ulid";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import {
  getEnvironments,
  getEnvironment,
  createEnvironment,
  updateEnvironment,
  getApprovers,
  addApprover,
  removeApprover,
} from "./stdb.js";

function requireUserAuth(req: any, res: any): { user_id: string; role: string } | null {
  const identity = extractUserIdentity(req.headers.authorization);
  if (!identity) {
    res.status(403).json({ error: "Agent tokens are not allowed to call the Promotion API" });
    return null;
  }
  return identity;
}

function validateEnvName(name: string): boolean {
  return /^[a-z0-9][a-z0-9-]{0,62}$/.test(name);
}

export function createEnvironmentsRouter(config: GatewayConfig): Router {
  const router = Router();

  // GET /api/v1/deployments/environments
  router.get("/", async (_req: any, res: any) => {
    try {
      const envs = await getEnvironments(config);
      const sorted = envs.sort((a, b) => a.order - b.order);

      // Attach approvers to each environment
      const result = await Promise.all(
        sorted.map(async env => {
          const approvers = await getApprovers(config, env.name);
          return { ...env, approvers };
        }),
      );
      res.json(result);
    } catch (err: any) {
      console.error("[environments] list failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/environments/:name
  router.get("/:name", async (req: any, res: any) => {
    try {
      const env = await getEnvironment(config, req.params.name);
      if (!env) return res.status(404).json({ error: "Environment not found" });
      const approvers = await getApprovers(config, env.name);
      res.json({ ...env, approvers });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/environments
  router.post("/", async (req: any, res: any) => {
    const identity = requireUserAuth(req, res);
    if (!identity) return;

    const {
      name, display_name, order,
      max_script_timeout = 600, health_check_interval = 300,
      deployment_window, required_approvals = 1,
    } = req.body;

    if (!name || !display_name || !order) {
      return res.status(400).json({ error: "name, display_name, and order are required" });
    }
    if (!validateEnvName(name)) {
      return res.status(400).json({ error: "name must be lowercase alphanumeric + hyphens" });
    }

    const existing = await getEnvironment(config, name);
    if (existing) {
      return res.status(400).json({ error: `Environment '${name}' already exists` });
    }

    // Check order uniqueness
    const allEnvs = await getEnvironments(config);
    const orderConflict = allEnvs.find(e => e.order === order && e.is_active);
    if (orderConflict) {
      return res.status(400).json({ error: `Order ${order} is already used by environment '${orderConflict.name}'` });
    }

    try {
      await createEnvironment(config, {
        name,
        display_name,
        order,
        max_script_timeout,
        health_check_interval,
        window_days: JSON.stringify(deployment_window?.days || []),
        window_start: deployment_window?.start || "",
        window_end: deployment_window?.end || "",
        window_timezone: deployment_window?.timezone || "",
        required_approvals,
      }, identity.user_id);

      const created = await getEnvironment(config, name);
      const approvers = await getApprovers(config, name);
      res.status(201).json({ ...created, approvers });
    } catch (err: any) {
      console.error("[environments] create failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/v1/deployments/environments/:name
  router.put("/:name", async (req: any, res: any) => {
    const identity = requireUserAuth(req, res);
    if (!identity) return;

    const { name } = req.params;
    const existing = await getEnvironment(config, name);
    if (!existing) return res.status(404).json({ error: "Environment not found" });

    const {
      display_name, order, max_script_timeout, health_check_interval,
      deployment_window, required_approvals, is_active,
    } = req.body;

    const updates: any = {};
    if (display_name !== undefined) updates.display_name = display_name;
    if (order !== undefined) updates.order = order;
    if (max_script_timeout !== undefined) updates.max_script_timeout = max_script_timeout;
    if (health_check_interval !== undefined) updates.health_check_interval = health_check_interval;
    if (required_approvals !== undefined) updates.required_approvals = required_approvals;
    if (is_active !== undefined) updates.is_active = is_active;
    if (deployment_window !== undefined) {
      updates.window_days = JSON.stringify(deployment_window.days || []);
      updates.window_start = deployment_window.start || "";
      updates.window_end = deployment_window.end || "";
      updates.window_timezone = deployment_window.timezone || "";
    }

    try {
      await updateEnvironment(config, name, updates, identity.user_id);
      const updated = await getEnvironment(config, name);
      const approvers = await getApprovers(config, name);
      res.json({ ...updated, approvers });
    } catch (err: any) {
      console.error("[environments] update failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/v1/deployments/environments/:name — soft delete
  router.delete("/:name", async (req: any, res: any) => {
    const identity = requireUserAuth(req, res);
    if (!identity) return;

    const { name } = req.params;
    const existing = await getEnvironment(config, name);
    if (!existing) return res.status(404).json({ error: "Environment not found" });

    try {
      await updateEnvironment(config, name, { is_active: false }, identity.user_id);
      res.json({ success: true, message: `Environment '${name}' deactivated` });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/environments/:name/approvers
  router.post("/:name/approvers", async (req: any, res: any) => {
    const identity = requireUserAuth(req, res);
    if (!identity) return;

    const { name } = req.params;
    const { user_id } = req.body;
    if (!user_id) return res.status(400).json({ error: "user_id is required" });

    const env = await getEnvironment(config, name);
    if (!env) return res.status(404).json({ error: "Environment not found" });

    try {
      await addApprover(config, name, user_id, identity.user_id);
      const approvers = await getApprovers(config, name);
      res.status(201).json({ approvers });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/v1/deployments/environments/:name/approvers/:user_id
  router.delete("/:name/approvers/:user_id", async (req: any, res: any) => {
    const identity = requireUserAuth(req, res);
    if (!identity) return;

    const { name, user_id } = req.params;
    const env = await getEnvironment(config, name);
    if (!env) return res.status(404).json({ error: "Environment not found" });

    try {
      await removeApprover(config, name, user_id);
      const approvers = await getApprovers(config, name);
      res.json({ approvers });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
