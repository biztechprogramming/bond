/**
 * Secrets Router — REST API for per-environment secrets management.
 *
 * Routes:
 *   GET    /secrets/:env              → list keys (values masked)
 *   POST   /secrets/:env/:key/reveal  → reveal single value (audit-logged)
 *   PUT    /secrets/:env/:key         → set/update secret
 *   DELETE /secrets/:env/:key         → delete secret
 *   POST   /secrets/:env/import       → import from discovery manifest
 *
 * Design Doc 045 §8.1
 */

import { Router } from "express";
import fs from "node:fs";
import path from "node:path";
import { homedir } from "node:os";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import { loadSecrets, encryptSecrets } from "./secrets.js";
import { readManifest } from "./manifest.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");
const AUDIT_DIR = path.join(DEPLOYMENTS_DIR, "secrets", "audit");

function auditLog(env: string, action: string, key: string, userId: string): void {
  fs.mkdirSync(AUDIT_DIR, { recursive: true });
  const entry = {
    timestamp: new Date().toISOString(),
    environment: env,
    action,
    key,
    user_id: userId,
  };
  const logPath = path.join(AUDIT_DIR, `${env}.jsonl`);
  fs.appendFileSync(logPath, JSON.stringify(entry) + "\n", "utf8");
}

function maskValue(value: string): string {
  if (value.length <= 4) return "****";
  return value.slice(0, 2) + "*".repeat(Math.min(value.length - 4, 20)) + value.slice(-2);
}

export function createSecretsRouter(_config: GatewayConfig): Router {
  const router = Router();

  // GET /secrets/:env — list keys with masked values
  router.get("/:env", (req: any, res: any) => {
    const { env } = req.params;
    try {
      const secrets = loadSecrets(env);
      const masked = Object.entries(secrets).map(([key, value]) => ({
        key,
        masked_value: maskValue(value),
        length: value.length,
      }));
      res.json({ environment: env, count: masked.length, secrets: masked });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /secrets/:env/:key/reveal — reveal single secret value (audit-logged)
  router.post("/:env/:key/reveal", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { env, key } = req.params;
    try {
      const secrets = loadSecrets(env);
      if (!(key in secrets)) {
        return res.status(404).json({ error: `Secret '${key}' not found in '${env}'` });
      }
      auditLog(env, "reveal", key, identity.user_id);
      res.json({ environment: env, key, value: secrets[key] });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // PUT /secrets/:env/:key — set or update a secret
  router.put("/:env/:key", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { env, key } = req.params;
    const { value } = req.body || {};
    if (value === undefined || typeof value !== "string") {
      return res.status(400).json({ error: "Body must include 'value' (string)" });
    }

    try {
      const secrets = loadSecrets(env);
      const isNew = !(key in secrets);
      secrets[key] = value;
      encryptSecrets(env, secrets);
      auditLog(env, isNew ? "create" : "update", key, identity.user_id);
      res.json({ success: true, environment: env, key, action: isNew ? "created" : "updated" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // DELETE /secrets/:env/:key — delete a secret
  router.delete("/:env/:key", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { env, key } = req.params;
    try {
      const secrets = loadSecrets(env);
      if (!(key in secrets)) {
        return res.status(404).json({ error: `Secret '${key}' not found in '${env}'` });
      }
      delete secrets[key];
      encryptSecrets(env, secrets);
      auditLog(env, "delete", key, identity.user_id);
      res.json({ success: true, environment: env, key, action: "deleted" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // POST /secrets/:env/import — import secrets from discovery manifest
  router.post("/:env/import", (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "User auth required" });

    const { env } = req.params;
    const { manifest_name, secrets: importSecrets } = req.body || {};

    if (!importSecrets || typeof importSecrets !== "object") {
      return res.status(400).json({ error: "Body must include 'secrets' object (key-value pairs)" });
    }

    try {
      const existing = loadSecrets(env);
      let imported = 0;
      let skipped = 0;
      for (const [key, value] of Object.entries(importSecrets as Record<string, string>)) {
        if (key in existing) {
          skipped++;
          continue;
        }
        existing[key] = value;
        imported++;
      }
      encryptSecrets(env, existing);
      auditLog(env, "import", `${imported} keys from ${manifest_name || "manual"}`, identity.user_id);
      res.json({ success: true, environment: env, imported, skipped, total: Object.keys(existing).length });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
