/**
 * Resource Router — CRUD + probe + recommendations endpoints for deployment resources.
 */

import { Router } from "express";
import path from "node:path";
import { homedir } from "node:os";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import {
  getResources,
  getResource,
  createResource,
  updateResource,
  deleteResource,
  updateResourceProbe,
} from "./resources.js";
import { probeResource } from "./resource-probe.js";
import { generateRecommendations, getRecommendationApplyScript } from "./recommendations.js";
import { registerScript } from "./scripts.js";
import { initiatePromotion } from "./stdb.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export function createResourceRouter(config: GatewayConfig): Router {
  const router = Router();

  // GET /api/v1/deployments/resources — list resources
  router.get("/", async (req: any, res: any) => {
    try {
      const environment = req.query.environment as string | undefined;
      const resources = await getResources(config, environment);
      res.json(resources);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/resources/:id — get single resource
  router.get("/:id", async (req: any, res: any) => {
    try {
      const resource = await getResource(config, req.params.id);
      if (!resource) return res.status(404).json({ error: "Resource not found" });
      res.json(resource);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/resources — create resource
  router.post("/", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { name, display_name, resource_type, environment, connection, tags } = req.body;
    if (!name || !resource_type || !environment) {
      return res.status(400).json({ error: "name, resource_type, and environment are required" });
    }

    try {
      const id = await createResource(config, {
        name,
        display_name: display_name || name,
        resource_type,
        environment,
        connection_json: JSON.stringify(connection || {}),
        capabilities_json: "{}",
        state_json: "{}",
        tags_json: JSON.stringify(tags || []),
        recommendations_json: "[]",
      });

      // Auto-probe for local type
      if (resource_type === "local") {
        try {
          const probe = await probeResource(connection, resource_type);
          const recommendations = generateRecommendations(probe);
          await updateResourceProbe(config, id, probe.capabilities, probe.state, recommendations);
        } catch (probeErr: any) {
          console.warn("[resources] Auto-probe failed:", probeErr.message);
        }
      }

      const resource = await getResource(config, id);
      res.status(201).json(resource);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/v1/deployments/resources/:id — update resource
  router.put("/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const existing = await getResource(config, req.params.id);
      if (!existing) return res.status(404).json({ error: "Resource not found" });

      const updates: any = {};
      const { name, display_name, environment, connection, tags } = req.body;
      if (name !== undefined) updates.name = name;
      if (display_name !== undefined) updates.display_name = display_name;
      if (environment !== undefined) updates.environment = environment;
      if (connection !== undefined) updates.connection_json = JSON.stringify(connection);
      if (tags !== undefined) updates.tags_json = JSON.stringify(tags);

      await updateResource(config, req.params.id, updates);
      const resource = await getResource(config, req.params.id);
      res.json(resource);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/v1/deployments/resources/:id — soft delete
  router.delete("/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const existing = await getResource(config, req.params.id);
      if (!existing) return res.status(404).json({ error: "Resource not found" });

      await deleteResource(config, req.params.id);
      res.json({ success: true, message: `Resource ${req.params.id} deactivated` });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/resources/:id/probe — trigger probe
  router.post("/:id/probe", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const resource = await getResource(config, req.params.id);
      if (!resource) return res.status(404).json({ error: "Resource not found" });

      const connection = JSON.parse(resource.connection_json);
      const probe = await probeResource(connection, resource.resource_type);
      const recommendations = generateRecommendations(probe);
      await updateResourceProbe(config, req.params.id, probe.capabilities, probe.state, recommendations);

      const updated = await getResource(config, req.params.id);
      res.json(updated);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/resources/:id/recommendations — get recommendations
  router.get("/:id/recommendations", async (req: any, res: any) => {
    try {
      const resource = await getResource(config, req.params.id);
      if (!resource) return res.status(404).json({ error: "Resource not found" });

      const recommendations = JSON.parse(resource.recommendations_json);
      res.json({ resource_id: req.params.id, recommendations });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/resources/:id/recommendations/:rank/apply
  // Generates a script from the recommendation and registers it in the script registry,
  // then auto-promotes it to the resource's environment.
  router.post("/:id/recommendations/:rank/apply", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const resource = await getResource(config, req.params.id);
      if (!resource) return res.status(404).json({ error: "Resource not found" });

      const rank = parseInt(req.params.rank, 10);
      const recommendations = JSON.parse(resource.recommendations_json);
      const result = getRecommendationApplyScript(recommendations, rank);

      if (!result) {
        return res.status(404).json({
          error: `Recommendation #${rank} not found or has no apply script`,
          resource_id: req.params.id,
        });
      }

      const { recommendation, script } = result;
      const scriptId = `rec-${resource.name}-${rank}-${Date.now()}`;
      const version = "v1";

      // Register the script in the script registry
      const manifest = registerScript(DEPLOYMENTS_DIR, {
        script_id: scriptId,
        version,
        name: `Apply: ${recommendation.title}`,
        description: `Auto-generated from recommendation for resource ${resource.display_name}: ${recommendation.description}`,
        timeout: 120,
        registered_by: identity.user_id,
        files: {
          "deploy.sh": Buffer.from(script, "utf-8"),
        },
      });

      // Auto-promote to the resource's environment
      const promotionId = await initiatePromotion(
        config,
        scriptId,
        version,
        manifest.sha256,
        resource.environment,
        "promoted",
        identity.user_id,
      );

      res.json({
        success: true,
        resource_id: req.params.id,
        recommendation: recommendation.title,
        script_id: scriptId,
        version,
        promotion_id: promotionId,
        environment: resource.environment,
        message: `Script registered and promoted to '${resource.environment}'. Deploy via the deploy agent.`,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
