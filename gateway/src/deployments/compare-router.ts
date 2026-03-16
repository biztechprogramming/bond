/**
 * Compare Router — environment comparison endpoints.
 *
 * Routes:
 *   GET /compare/:envA/:envB           → full comparison
 *   GET /compare/:envA/:envB/scripts   → script version comparison
 *   GET /compare/:envA/:envB/secrets   → secret key comparison
 *
 * Design Doc 045 §8.3
 */

import { Router } from "express";
import type { GatewayConfig } from "../config/index.js";
import { compareEnvironments } from "./compare.js";

export function createCompareRouter(config: GatewayConfig): Router {
  const router = Router();

  // GET /compare/:envA/:envB — full environment comparison
  router.get("/:envA/:envB", async (req: any, res: any) => {
    const { envA, envB } = req.params;
    try {
      const result = await compareEnvironments(config, envA, envB);
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /compare/:envA/:envB/scripts — script-only comparison
  router.get("/:envA/:envB/scripts", async (req: any, res: any) => {
    const { envA, envB } = req.params;
    try {
      const result = await compareEnvironments(config, envA, envB);
      res.json({ envA, envB, scripts: result.scripts });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /compare/:envA/:envB/secrets — secret key comparison
  router.get("/:envA/:envB/secrets", async (req: any, res: any) => {
    const { envA, envB } = req.params;
    try {
      const result = await compareEnvironments(config, envA, envB);
      res.json({ envA, envB, secrets: result.secrets });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
