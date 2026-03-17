/**
 * Alert Rules Router — CRUD endpoints for deployment alert rules.
 *
 * Routes:
 *   GET    /alert-rules/:env              → list rules
 *   POST   /alert-rules/:env              → create rule
 *   PUT    /alert-rules/:env/:id          → update rule
 *   DELETE /alert-rules/:env/:id          → delete rule
 *   PUT    /alert-rules/:env/:id/enable   → enable rule
 *   PUT    /alert-rules/:env/:id/disable  → disable rule
 *
 * Design Doc 045 §8.2
 */

import { Router } from "express";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import {
  getAlertRules,
  getAlertRule,
  createAlertRule,
  updateAlertRule,
  deleteAlertRule,
  setAlertRuleEnabled,
} from "./alert-rules.js";

export function createAlertRulesRouter(config: GatewayConfig): Router {
  const router = Router();

  // GET /alert-rules/:env — list alert rules for environment
  router.get("/:env", async (req: any, res: any) => {
    const { env } = req.params;
    try {
      const rules = await getAlertRules(config, env);
      res.json({ environment: env, count: rules.length, rules });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /alert-rules/:env — create alert rule
  router.post("/:env", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { env } = req.params;
    const { name, metric, operator, threshold, duration_minutes, severity, enabled, auto_file_issue, custom_script_id, applies_to_resources } = req.body || {};

    if (!name || !metric || !operator || threshold === undefined) {
      return res.status(400).json({ error: "name, metric, operator, and threshold are required" });
    }

    try {
      const id = await createAlertRule(config, {
        environment: env,
        name,
        metric,
        operator,
        threshold,
        duration_minutes: duration_minutes ?? 0,
        severity: severity || "medium",
        enabled: enabled !== false,
        auto_file_issue: auto_file_issue || false,
        custom_script_id: custom_script_id || "",
        applies_to_resources: applies_to_resources || "",
      });
      const rule = await getAlertRule(config, id);
      res.status(201).json(rule);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /alert-rules/:env/:id — update alert rule
  router.put("/:env/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { id } = req.params;
    try {
      const existing = await getAlertRule(config, id);
      if (!existing) return res.status(404).json({ error: "Alert rule not found" });

      const { name, metric, operator, threshold, duration_minutes, severity, enabled, auto_file_issue, custom_script_id, applies_to_resources } = req.body || {};
      const updates: any = {};
      if (name !== undefined) updates.name = name;
      if (metric !== undefined) updates.metric = metric;
      if (operator !== undefined) updates.operator = operator;
      if (threshold !== undefined) updates.threshold = threshold;
      if (duration_minutes !== undefined) updates.duration_minutes = duration_minutes;
      if (severity !== undefined) updates.severity = severity;
      if (enabled !== undefined) updates.enabled = enabled;
      if (auto_file_issue !== undefined) updates.auto_file_issue = auto_file_issue;
      if (custom_script_id !== undefined) updates.custom_script_id = custom_script_id;
      if (applies_to_resources !== undefined) updates.applies_to_resources = applies_to_resources;

      await updateAlertRule(config, id, updates);
      const rule = await getAlertRule(config, id);
      res.json(rule);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /alert-rules/:env/:id — delete alert rule
  router.delete("/:env/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { id } = req.params;
    try {
      const existing = await getAlertRule(config, id);
      if (!existing) return res.status(404).json({ error: "Alert rule not found" });

      await deleteAlertRule(config, id);
      res.json({ success: true, message: `Alert rule ${id} deleted` });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /alert-rules/:env/:id/enable — enable rule
  router.put("/:env/:id/enable", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { id } = req.params;
    try {
      const existing = await getAlertRule(config, id);
      if (!existing) return res.status(404).json({ error: "Alert rule not found" });

      await setAlertRuleEnabled(config, id, true);
      res.json({ success: true, id, enabled: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /alert-rules/:env/:id/disable — disable rule
  router.put("/:env/:id/disable", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { id } = req.params;
    try {
      const existing = await getAlertRule(config, id);
      if (!existing) return res.status(404).json({ error: "Alert rule not found" });

      await setAlertRuleEnabled(config, id, false);
      res.json({ success: true, id, enabled: false });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
