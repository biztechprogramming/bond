/**
 * Script Registry API — user-session-auth for writes, open reads.
 */

import { Router } from "express";
import path from "node:path";
import { homedir } from "node:os";
import type { GatewayConfig } from "../config/index.js";
import { extractUserIdentity } from "./session-tokens.js";
import {
  registerScript,
  getManifest,
  listScripts,
  verifyScriptHash,
  parseScriptMeta,
} from "./scripts.js";

const DEPLOYMENTS_DIR = path.join(homedir(), ".bond", "deployments");

export function createScriptsRouter(_config: GatewayConfig): Router {
  const router = Router();

  // GET /api/v1/deployments/scripts — list all registered scripts
  router.get("/", (_req: any, res: any) => {
    try {
      const scripts = listScripts(DEPLOYMENTS_DIR);
      const result = scripts.map(s => {
        const manifests = s.versions.map(v => getManifest(DEPLOYMENTS_DIR, s.script_id, v));
        return { script_id: s.script_id, versions: s.versions, latest: manifests[manifests.length - 1] };
      });
      res.json(result);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/scripts/:scriptId — script info
  router.get("/:scriptId", (req: any, res: any) => {
    try {
      const scripts = listScripts(DEPLOYMENTS_DIR);
      const script = scripts.find(s => s.script_id === req.params.scriptId);
      if (!script) return res.status(404).json({ error: "Script not found" });
      const manifests = script.versions.map(v => getManifest(DEPLOYMENTS_DIR, script.script_id, v));
      res.json({ script_id: script.script_id, versions: script.versions, manifests });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  // GET /api/v1/deployments/scripts/:scriptId/:version — manifest
  router.get("/:scriptId/:version", (req: any, res: any) => {
    const { scriptId, version } = req.params;
    const manifest = getManifest(DEPLOYMENTS_DIR, scriptId, version);
    if (!manifest) return res.status(404).json({ error: "Script version not found" });

    const valid = verifyScriptHash(DEPLOYMENTS_DIR, scriptId, version);
    res.json({ ...manifest, hash_valid: valid });
  });

  // POST /api/v1/deployments/scripts — register a new script
  // Body: multipart/json { script_id, version, name, files: {filename: base64content} }
  router.post("/", async (req: any, res: any) => {
    const identity = extractUserIdentity(req.headers.authorization);
    if (!identity) return res.status(403).json({ error: "Authentication required to register scripts" });

    const { script_id, version, name, description, timeout, depends_on, rollback, dry_run, health_check, files } = req.body;

    if (!script_id || !version || !files) {
      return res.status(400).json({ error: "script_id, version, and files are required" });
    }
    if (!files["deploy.sh"]) {
      return res.status(400).json({ error: "files must include deploy.sh" });
    }

    try {
      const fileBuffers: Record<string, Buffer> = {};
      for (const [filename, content] of Object.entries(files as Record<string, string>)) {
        fileBuffers[filename] = Buffer.from(content, "base64");
      }

      // Auto-parse metadata from deploy.sh if not provided
      const meta = parseScriptMeta(fileBuffers["deploy.sh"]!.toString("utf8"));
      const manifest = registerScript(DEPLOYMENTS_DIR, {
        script_id,
        version,
        name: name || meta["name"] || script_id,
        description,
        timeout: timeout ?? (meta["timeout"] ? parseInt(meta["timeout"]) : undefined),
        depends_on: depends_on ?? (meta["depends_on"] ? [meta["depends_on"]] : []),
        rollback: rollback ?? meta["rollback"],
        dry_run: dry_run ?? (meta["dry_run"] === "true"),
        health_check: health_check ?? meta["health_check"],
        registered_by: identity.user_id,
        files: fileBuffers,
      });
      res.status(201).json(manifest);
    } catch (err: any) {
      console.error("[scripts] register failed:", err.message);
      res.status(400).json({ error: err.message });
    }
  });

  return router;
}
