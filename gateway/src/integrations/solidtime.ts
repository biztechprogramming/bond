/**
 * SolidTime integration routes.
 */
import { Router } from "express";
import type { Request, Response } from "express";
import { getSolidTimeConfig, setSolidTimeConfig, removeSolidTimeConfig } from "./index.js";
import type { SolidTimeConfig } from "./index.js";

export function createSolidTimeRouter(): Router {
  const router = Router();

  // POST /integrations/solidtime/setup — validate and save config
  router.post("/integrations/solidtime/setup", async (req: Request, res: Response) => {
    try {
      const { url, apiToken } = req.body;
      if (!url || typeof url !== "string") {
        res.status(400).json({ error: "Missing or invalid url" });
        return;
      }
      if (!apiToken || typeof apiToken !== "string") {
        res.status(400).json({ error: "Missing or invalid apiToken" });
        return;
      }

      const baseUrl = url.replace(/\/+$/, "");
      const token = apiToken.startsWith("Bearer ") ? apiToken : `Bearer ${apiToken}`;

      // Validate by calling /api/v1/users/me
      const meRes = await fetch(`${baseUrl}/api/v1/users/me`, {
        headers: { Authorization: token, Accept: "application/json" },
      });
      if (!meRes.ok) {
        res.status(400).json({ error: `SolidTime auth failed: ${meRes.status} ${meRes.statusText}` });
        return;
      }
      const meData = await meRes.json();
      const userName = meData.data?.name || meData.data?.email || "Unknown";

      // Get memberships to find org ID
      const membershipsRes = await fetch(`${baseUrl}/api/v1/users/me/memberships`, {
        headers: { Authorization: token, Accept: "application/json" },
      });
      if (!membershipsRes.ok) {
        res.status(400).json({ error: "Failed to fetch memberships" });
        return;
      }
      const membershipsData = await membershipsRes.json();
      const memberships = membershipsData.data || [];
      if (memberships.length === 0) {
        res.status(400).json({ error: "No organizations found for this user" });
        return;
      }

      const membership = memberships[0];
      const organizationId = membership.organization_id || membership.organization?.id;
      const memberId = membership.id || membership.member_id;
      const organizationName = membership.organization?.name || "Organization";

      if (!organizationId || !memberId) {
        res.status(400).json({ error: "Could not determine organization or member ID" });
        return;
      }

      const config: SolidTimeConfig = {
        type: "solidtime",
        enabled: true,
        url: baseUrl,
        apiToken: token,
        organizationId,
        memberId,
        organizationName,
        userName,
      };

      setSolidTimeConfig(config);
      res.json({ ok: true, organization: organizationName, user: userName });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Setup failed" });
    }
  });

  // POST /integrations/solidtime/test — test the connection
  router.post("/integrations/solidtime/test", async (_req: Request, res: Response) => {
    try {
      const config = getSolidTimeConfig();
      if (!config) {
        res.status(404).json({ error: "SolidTime not configured" });
        return;
      }

      const meRes = await fetch(`${config.url}/api/v1/users/me`, {
        headers: { Authorization: config.apiToken, Accept: "application/json" },
      });
      if (!meRes.ok) {
        res.status(400).json({ error: `Connection failed: ${meRes.status}` });
        return;
      }
      res.json({ ok: true });
    } catch (err) {
      res.status(500).json({ error: err instanceof Error ? err.message : "Test failed" });
    }
  });

  // GET /integrations/solidtime/status — return config status
  router.get("/integrations/solidtime/status", (_req: Request, res: Response) => {
    const config = getSolidTimeConfig();
    if (!config) {
      res.json({ configured: false });
      return;
    }
    res.json({
      configured: true,
      enabled: config.enabled,
      organizationName: config.organizationName,
      userName: config.userName,
    });
  });

  // GET /integrations/solidtime/config — full config for backend tools
  router.get("/integrations/solidtime/config", (_req: Request, res: Response) => {
    const config = getSolidTimeConfig();
    if (!config) {
      res.status(404).json({ error: "Not configured" });
      return;
    }
    res.json(config);
  });

  // DELETE /integrations/solidtime — remove config
  router.delete("/integrations/solidtime", (_req: Request, res: Response) => {
    removeSolidTimeConfig();
    res.json({ ok: true });
  });

  return router;
}
