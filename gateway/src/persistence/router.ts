/**
 * SpacetimeDB Persistence API
 *
 * Uses SpacetimeDB's HTTP reducer call API for reliable writes.
 * The WS SDK has compatibility issues with Node 18's WebSocket polyfills,
 * so we call reducers directly via HTTP POST to /v1/database/{name}/call/{reducer}.
 *
 * SpacetimeDB HTTP API expects reducer args as a JSON array of positional values.
 */

import { Router } from "express";
import { ulid } from "ulid";
import type { GatewayConfig } from "../config/index.js";

async function callReducer(
  baseUrl: string,
  moduleName: string,
  reducerName: string,
  args: (string | number | boolean)[]
): Promise<void> {
  const url = `${baseUrl}/v1/database/${moduleName}/call/${reducerName}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`SpacetimeDB ${reducerName} failed (${res.status}): ${body}`);
  }
}

export function createPersistenceRouter(config: GatewayConfig) {
  const router = Router();
  const { spacetimedbUrl, spacetimedbModuleName } = config;

  console.log(`[persistence] Using SpacetimeDB HTTP API at ${spacetimedbUrl} module ${spacetimedbModuleName}`);

  // Startup health check
  fetch(`${spacetimedbUrl}/v1/health`)
    .then((r) => r.json())
    .then((h: any) => console.log(`[persistence] SpacetimeDB healthy: v${h.version}`))
    .catch((err) => console.error(`[persistence] SpacetimeDB unreachable: ${err.message}`));

  /**
   * POST /api/v1/messages
   */
  router.post("/messages", async (req: any, res: any) => {
    const { agentId, sessionId, role, content, metadata } = req.body;
    const id = ulid();

    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "save_message", [
        id,
        agentId,
        sessionId,
        role,
        content,
        JSON.stringify(metadata || {}),
      ]);
      console.log(`[persistence] Saved message ${id} for agent ${agentId}`);
      res.status(201).json({ id, status: "saved", timestamp: new Date().toISOString() });
    } catch (err: any) {
      console.error(`[persistence] save_message failed: ${err.message}`);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /api/v1/tool-logs
   */
  router.post("/tool-logs", async (req: any, res: any) => {
    const { agentId, sessionId, toolName, input, output, duration } = req.body;
    const id = ulid();

    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "log_tool", [
        id,
        agentId,
        sessionId,
        toolName,
        JSON.stringify(input),
        JSON.stringify(output),
        Math.round((duration || 0) * 1000), // seconds → ms for u32
      ]);
      console.log(`[persistence] Logged tool ${toolName} (${id})`);
      res.status(201).json({ id, status: "logged" });
    } catch (err: any) {
      console.error(`[persistence] log_tool failed: ${err.message}`);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /api/v1/settings
   */
  router.post("/settings", async (req: any, res: any) => {
    const { key, value } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "set_setting", [
        key,
        typeof value === "string" ? value : JSON.stringify(value),
      ]);
      res.status(200).json({ status: "saved" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * DELETE /api/v1/settings/:key
   */
  router.delete("/settings/:key", async (req: any, res: any) => {
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "delete_setting", [req.params.key]);
      res.status(204).end();
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /api/v1/mcp
   */
  router.post("/mcp", async (req: any, res: any) => {
    const { name, command, args, env, agentId } = req.body;
    const id = ulid();
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "add_mcp_server", [
        id,
        name,
        command,
        JSON.stringify(args || []),
        JSON.stringify(env || {}),
        agentId || "",
      ]);
      res.status(201).json({ id, status: "added" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
