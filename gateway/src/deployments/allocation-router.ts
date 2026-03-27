/**
 * Allocation Router — CRUD + conflict check + suggest endpoints for environment allocations (Doc 077).
 */

import { Router } from "express";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import {
  getEnvironmentAllocations,
  getEnvironmentAllocation,
  createEnvironmentAllocation,
  updateEnvironmentAllocation,
  deactivateEnvironmentAllocation,
  deleteEnvironmentAllocation,
  getServicePortAssignments,
  createServicePortAssignment,
  updateServicePortAssignment,
  deleteServicePortAssignment,
  getAllocationHistory,
  checkPortConflicts,
  suggestDefaults,
  type ConflictCheckRequest,
  type SuggestDefaultsRequest,
} from "./stdb.js";

export function createAllocationRouter(config: GatewayConfig): Router {
  const router = Router();

  // GET /api/v1/deployments/allocations?resource_id=X&app_name=Y
  router.get("/", async (req: any, res: any) => {
    try {
      const resourceId = req.query.resource_id as string | undefined;
      const appName = req.query.app_name as string | undefined;
      const allocations = await getEnvironmentAllocations(config, resourceId, appName);
      res.json(allocations);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/allocations/:id
  router.get("/:id", async (req: any, res: any) => {
    try {
      const alloc = await getEnvironmentAllocation(config, req.params.id);
      if (!alloc) return res.status(404).json({ error: "Allocation not found" });
      res.json(alloc);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/allocations
  router.post("/", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { resource_id, app_name, environment_name, base_port, app_dir, data_dir, log_dir, config_dir, tls_cert_path, tls_key_path } = req.body;
    if (!resource_id || !app_name || !environment_name) {
      return res.status(400).json({ error: "resource_id, app_name, and environment_name are required" });
    }

    // Validate TLS path pairing
    if ((tls_cert_path && !tls_key_path) || (!tls_cert_path && tls_key_path)) {
      return res.status(400).json({ error: "Both tls_cert_path and tls_key_path must be provided together" });
    }

    try {
      const id = await createEnvironmentAllocation(config, {
        resource_id,
        app_name,
        environment_name,
        base_port: base_port ?? 3000,
        app_dir: app_dir || `/opt/${app_name}/${environment_name}`,
        data_dir: data_dir || `/var/data/${app_name}/${environment_name}`,
        log_dir: log_dir || `/var/log/${app_name}/${environment_name}`,
        config_dir: config_dir || `/etc/${app_name}/${environment_name}`,
        tls_cert_path: tls_cert_path || "",
        tls_key_path: tls_key_path || "",
      }, identity.user_id);
      res.json({ id, success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/v1/deployments/allocations/:id
  router.put("/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      await updateEnvironmentAllocation(config, req.params.id, req.body, identity.user_id);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/v1/deployments/allocations/:id
  router.delete("/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      const alloc = await getEnvironmentAllocation(config, req.params.id);
      if (!alloc) return res.status(404).json({ error: "Allocation not found" });

      if (alloc.is_active) {
        // Deactivate first, don't hard delete active allocations
        await deactivateEnvironmentAllocation(config, req.params.id, identity.user_id);
        res.json({ success: true, message: "Allocation deactivated" });
      } else {
        await deleteEnvironmentAllocation(config, req.params.id);
        res.json({ success: true, message: "Allocation deleted" });
      }
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/allocations/:id/ports
  router.get("/:id/ports", async (req: any, res: any) => {
    try {
      const ports = await getServicePortAssignments(config, req.params.id);
      res.json(ports);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/allocations/:id/ports
  router.post("/:id/ports", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { service_name, port, protocol, data_dir, health_endpoint, description } = req.body;
    if (!service_name || !port) {
      return res.status(400).json({ error: "service_name and port are required" });
    }

    try {
      const id = await createServicePortAssignment(config, {
        allocation_id: req.params.id,
        service_name,
        port,
        protocol: protocol || "tcp",
        data_dir: data_dir || "",
        health_endpoint: health_endpoint || "",
        description: description || "",
      });
      res.json({ id, success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /api/v1/deployments/allocations/ports/:id (note: port ID, not allocation ID)
  router.put("/ports/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      await updateServicePortAssignment(config, req.params.id, req.body);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /api/v1/deployments/allocations/ports/:id
  router.delete("/ports/:id", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    try {
      await deleteServicePortAssignment(config, req.params.id);
      res.json({ success: true });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/allocations/check-conflicts
  router.post("/check-conflicts", async (req: any, res: any) => {
    const { resource_id, app_name, environment_name, ports, directories, exclude_allocation_id } = req.body;
    if (!resource_id || !app_name || !environment_name) {
      return res.status(400).json({ error: "resource_id, app_name, and environment_name are required" });
    }

    try {
      const result = await checkPortConflicts(config, {
        resource_id,
        app_name,
        environment_name,
        ports: ports || [],
        directories: directories || { app_dir: "", data_dir: "", log_dir: "", config_dir: "" },
        exclude_allocation_id,
      });
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /api/v1/deployments/allocations/suggest
  router.post("/suggest", async (req: any, res: any) => {
    const { resource_id, app_name, environment_name, base_port, services } = req.body;
    if (!resource_id || !app_name || !environment_name) {
      return res.status(400).json({ error: "resource_id, app_name, and environment_name are required" });
    }

    try {
      const result = await suggestDefaults(config, {
        resource_id,
        app_name,
        environment_name,
        base_port,
        services,
      });
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/allocations/:id/history
  router.get("/:id/history", async (req: any, res: any) => {
    try {
      const limit = parseInt(req.query.limit as string || "50", 10);
      const history = await getAllocationHistory(config, req.params.id, limit);
      res.json({ entries: history });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
