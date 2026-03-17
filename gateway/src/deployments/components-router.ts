/**
 * Components Router — CRUD endpoints for component entities.
 *
 * Design Doc 045a
 */

import { Router } from "express";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import {
  getComponents,
  getComponent,
  getComponentByName,
  createComponent,
  updateComponent,
  deactivateComponent,
  getComponentTree,
  getComponentResources,
  addComponentResource,
  removeComponentResource,
  getComponentScripts,
  addComponentScript,
  removeComponentScript,
  getComponentSecrets,
  addComponentSecret,
  removeComponentSecret,
  getComponentStatus,
} from "./components.js";

export function createComponentsRouter(config: GatewayConfig): Router {
  const router = Router();

  // GET /api/v1/deployments/components — list or tree
  router.get("/", async (req: any, res: any) => {
    try {
      const environment = req.query.environment as string | undefined;
      const tree = req.query.tree === "true";
      if (tree) {
        const nodes = await getComponentTree(config, environment);
        return res.json(nodes);
      }
      const components = await getComponents(config, environment);
      res.json(components);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/components/:id — single + all links
  router.get("/:id", async (req: any, res: any) => {
    try {
      const component = await getComponent(config, req.params.id);
      if (!component) return res.status(404).json({ error: "Component not found" });
      const [resources, scripts, secrets] = await Promise.all([
        getComponentResources(config, req.params.id),
        getComponentScripts(config, req.params.id),
        getComponentSecrets(config, req.params.id),
      ]);
      res.json({ ...component, resources, scripts, secrets });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/components — create
  router.post("/", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { name, display_name, component_type } = req.body;
    if (!name || !component_type) {
      return res.status(400).json({ error: "name and component_type are required" });
    }

    try {
      const id = await createComponent(config, {
        name,
        display_name: display_name || name,
        component_type,
        parent_id: req.body.parent_id,
        runtime: req.body.runtime,
        framework: req.body.framework,
        repository_url: req.body.repository_url,
        icon: req.body.icon,
        description: req.body.description,
        discovered_from: req.body.discovered_from,
        source_path: req.body.source_path,
      });
      const component = await getComponent(config, id);
      res.status(201).json(component);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/v1/deployments/components/:id — update
  router.put("/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const existing = await getComponent(config, req.params.id);
      if (!existing) return res.status(404).json({ error: "Component not found" });

      const updates: any = {};
      for (const key of ["display_name", "component_type", "parent_id", "runtime", "framework", "repository_url", "icon", "description"]) {
        if (req.body[key] !== undefined) updates[key] = req.body[key];
      }

      await updateComponent(config, req.params.id, updates);
      const component = await getComponent(config, req.params.id);
      res.json(component);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/v1/deployments/components/:id — deactivate
  router.delete("/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const existing = await getComponent(config, req.params.id);
      if (!existing) return res.status(404).json({ error: "Component not found" });

      await deactivateComponent(config, req.params.id);
      res.json({ success: true, message: `Component ${req.params.id} deactivated` });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/components/:id/status
  router.get("/:id/status", async (req: any, res: any) => {
    try {
      const environment = req.query.environment as string;
      if (!environment) return res.status(400).json({ error: "environment query param required" });
      const status = await getComponentStatus(config, req.params.id, environment);
      res.json(status);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Resource links ──────────────────────────────────────────────────────────

  router.get("/:id/resources", async (req: any, res: any) => {
    try {
      const links = await getComponentResources(config, req.params.id);
      res.json(links);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.post("/:id/resources", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      const id = await addComponentResource(config, {
        component_id: req.params.id,
        resource_id: req.body.resource_id,
        environment: req.body.environment,
        port: req.body.port,
        process_name: req.body.process_name,
        health_check: req.body.health_check,
      });
      res.status(201).json({ id });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.delete("/:id/resources/:linkId", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      await removeComponentResource(config, req.params.linkId);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Script links ────────────────────────────────────────────────────────────

  router.get("/:id/scripts", async (req: any, res: any) => {
    try {
      const links = await getComponentScripts(config, req.params.id);
      res.json(links);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.post("/:id/scripts", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      const id = await addComponentScript(config, {
        component_id: req.params.id,
        script_id: req.body.script_id,
        role: req.body.role || "deploy",
      });
      res.status(201).json({ id });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.delete("/:id/scripts/:linkId", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      await removeComponentScript(config, req.params.linkId);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // ── Secret links ────────────────────────────────────────────────────────────

  router.get("/:id/secrets", async (req: any, res: any) => {
    try {
      const environment = req.query.environment as string | undefined;
      const links = await getComponentSecrets(config, req.params.id, environment);
      res.json(links);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.post("/:id/secrets", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      const id = await addComponentSecret(config, {
        component_id: req.params.id,
        secret_key: req.body.secret_key,
        environment: req.body.environment,
        is_sensitive: req.body.is_sensitive !== false,
      });
      res.status(201).json({ id });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  router.delete("/:id/secrets/:linkId", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });
    try {
      await removeComponentSecret(config, req.params.linkId);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
