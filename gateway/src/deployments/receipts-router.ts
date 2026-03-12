/**
 * Receipts API — read-only access to deployment receipts.
 */

import { Router } from "express";
import type { Request, Response } from "express";
import path from "node:path";
import { homedir } from "node:os";
import type { GatewayConfig } from "../config/index.js";
import { listReceipts, readReceipt } from "./receipts.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export function createReceiptsRouter(_config: GatewayConfig): Router {
  const router = Router();

  // GET /api/v1/deployments/receipts/:env — list receipts for environment
  router.get("/:env", (req: any, res: any) => {
    try {
      const { env } = req.params;
      const limit = parseInt(req.query.limit as string || "50", 10);
      const receipts = listReceipts(DEPLOYMENTS_DIR, env, limit);
      res.json(receipts);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/receipts/:env/:receiptId — single receipt
  router.get("/:env/:receiptId", (req: any, res: any) => {
    const { env, receiptId } = req.params;
    const receipt = readReceipt(DEPLOYMENTS_DIR, env, receiptId);
    if (!receipt) return res.status(404).json({ error: "Receipt not found" });
    res.json(receipt);
  });

  return router;
}
