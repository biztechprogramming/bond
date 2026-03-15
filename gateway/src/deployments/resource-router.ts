/**
 * Resource Router — CRUD + probe endpoints for deployment resources.
 */

import { Router } from "express";
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

  // POST /api/v1/deployments/resources/:id/recommendations/:rank/apply — stub
  router.post("/:id/recommendations/:rank/apply", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    res.json({
      success: false,
      message: "Recommendation application is not yet implemented",
      resource_id: req.params.id,
      rank: req.params.rank,
    });
  });

  return router;
}

/**
 * Generate simple recommendations based on probe results.
 */
function generateRecommendations(probe: { capabilities: Record<string, any>; state: Record<string, any> }): any[] {
  const recs: any[] = [];
  const caps = probe.capabilities;
  const state = probe.state;

  if (!caps.docker) {
    recs.push({ rank: recs.length + 1, title: "Install Docker", description: "Docker is not detected. Install it to enable containerized deployments.", severity: "medium" });
  }
  if (!caps.node) {
    recs.push({ rank: recs.length + 1, title: "Install Node.js", description: "Node.js is not detected. Required for JavaScript/TypeScript deployments.", severity: "low" });
  }
  if (state.memory_gb !== undefined && typeof state.memory_gb === "number" && state.memory_gb < 2) {
    recs.push({ rank: recs.length + 1, title: "Low Memory", description: `Only ${state.memory_gb}GB RAM detected. Consider upgrading for production workloads.`, severity: "high" });
  }

  return recs;
}
