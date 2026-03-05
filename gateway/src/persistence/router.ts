/**
 * SpacetimeDB Persistence API
 *
 * Uses SpacetimeDB's HTTP reducer call API for reliable writes.
 */

import { Router } from "express";
import { ulid } from "ulid";
import type { GatewayConfig } from "../config/index.js";

import { callReducer, sqlQuery } from "../spacetimedb/client.js";

function safeParseJson(val: any, fallback: any): any {
  if (val === null || val === undefined || val === "") return fallback;
  if (typeof val !== "string") return val;
  try { return JSON.parse(val); } catch { return fallback; }
}

export function createPersistenceRouter(config: GatewayConfig) {
  const router = Router();
  const { spacetimedbUrl, spacetimedbModuleName, spacetimedbToken: token } = config;

  console.log(`[persistence] Using SpacetimeDB HTTP API at ${spacetimedbUrl} module ${spacetimedbModuleName}`);

  /**
   * POST /messages
   */
  router.post("/messages", async (req: any, res: any) => {
    const { agentId, sessionId, role, content, metadata } = req.body;
    const id = ulid();

    try {
      // Save to conversationMessages table (main conversation history)
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "add_conversation_message", [
        id,
        sessionId, // conversationId
        role,
        content,
        "", // tool_calls
        "", // tool_call_id
        0,  // token_count
        "delivered"
      ], token);
      
      // Also save to messages table for logging/debugging
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "save_message", [
        id,
        agentId || "",
        sessionId,
        role,
        content,
        JSON.stringify(metadata || {}),
      ], token);
      
      res.status(201).json({ id, status: "saved" });
    } catch (err: any) {
      console.error(`[persistence] save_message failed:`, err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /tool-logs
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
        Math.round((duration || 0) * 1000),
      ], token);
      res.status(201).json({ id, status: "logged" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /settings/:key
   */
  router.get("/settings/:key", async (req: any, res: any) => {
    const { key } = req.params;
    try {
      // Escape single quotes by doubling them (SQL standard)
      const escapedKey = key.replace(/'/g, "''");
      const rows = await sqlQuery(
        spacetimedbUrl,
        spacetimedbModuleName,
        `SELECT key, value, key_type FROM settings WHERE key = '${escapedKey}'`,
        token
      );
      if (rows.length === 0) {
        res.status(404).json({ error: `Setting ${key} not found` });
        return;
      }
      const row = rows[0];
      res.json({
        key: row.key,
        value: row.value,
        keyType: row.key_type,
      });
    } catch (err: any) {
      console.error(`[persistence] GET /settings/${key} failed:`, err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /provider-api-keys/:providerId
   */
  router.get("/provider-api-keys/:providerId", async (req: any, res: any) => {
    const { providerId } = req.params;
    try {
      // Escape single quotes by doubling them (SQL standard)
      const escapedProviderId = providerId.replace(/'/g, "''");
      const rows = await sqlQuery(
        spacetimedbUrl,
        spacetimedbModuleName,
        `SELECT provider_id, encrypted_value, key_type FROM provider_api_keys WHERE provider_id = '${escapedProviderId}'`,
        token
      );
      if (rows.length === 0) {
        res.status(404).json({ error: `API key for provider ${providerId} not found` });
        return;
      }
      const row = rows[0];
      res.json({
        providerId: row.provider_id,
        encryptedValue: row.encrypted_value,
        keyType: row.key_type,
      });
    } catch (err: any) {
      console.error(`[persistence] GET /provider-api-keys/${providerId} failed:`, err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /settings
   */
  router.post("/settings", async (req: any, res: any) => {
    const { key, value } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "set_setting", [
        key,
        typeof value === "string" ? value : JSON.stringify(value),
      ], token);
      res.status(200).json({ status: "saved" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * DELETE /settings/:key
   */
  router.delete("/settings/:key", async (req: any, res: any) => {
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "delete_setting", [req.params.key], token);
      res.status(204).end();
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /mcp?agent_id=X
   * List MCP servers for an agent (global + agent-specific).
   */
  router.get("/mcp", async (req: any, res: any) => {
    const { agent_id } = req.query;
    try {
      const servers = await sqlQuery(spacetimedbUrl, spacetimedbModuleName, "SELECT * FROM mcp_servers", token);
      const filtered = servers.filter((s: any) => {
        const isEnabled = s.enabled === true || s.enabled === 1;
        const isGlobal = s.agent_id === null || s.agent_id === "" || s.agent_id === undefined;
        const isForAgent = agent_id ? s.agent_id === agent_id : false;
        return isEnabled && (isGlobal || isForAgent);
      });
      res.json(
        filtered.map((s: any) => ({
          id: s.id,
          name: s.name,
          command: s.command,
          args: safeParseJson(s.args, []),
          env: safeParseJson(s.env, {}),
          enabled: s.enabled === true || s.enabled === 1,
          agent_id: s.agent_id || null,
        }))
      );
    } catch (err: any) {
      console.error("[persistence] GET /mcp failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /mcp
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

  /**
   * POST /sync/agents
   */
  router.post("/sync/agents", async (req: any, res: any) => {
    const { id, name, displayName, systemPrompt, model, utilityModel, tools, isDefault } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "add_agent", [
        id,
        name,
        displayName,
        systemPrompt,
        model,
        utilityModel,
        typeof tools === "string" ? tools : JSON.stringify(tools || []),
        !!isDefault
      ]);
      res.status(201).json({ status: "synced" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /sync/models
   */
  router.post("/sync/models", async (req: any, res: any) => {
    const { id, provider, modelId, displayName, contextWindow, isEnabled } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "add_model", [
        id,
        provider,
        modelId,
        displayName,
        contextWindow,
        isEnabled
      ], token);
      res.status(201).json({ status: "synced" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /sync/conversations
   */
  router.post("/sync/conversations", async (req: any, res: any) => {
    const { id, agentId, channel, title } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "create_conversation", [
        id,
        agentId,
        channel || "webchat",
        title || ""
      ], token);
      res.status(201).json({ status: "synced" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /sync/messages
   */
  router.post("/sync/messages", async (req: any, res: any) => {
    const { id, conversationId, role, content, status } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "add_conversation_message", [
        id,
        conversationId,
        role,
        content,
        "", // tool_calls
        "", // tool_call_id
        0,  // token_count
        status || "delivered"
      ], token);
      res.status(201).json({ status: "synced" });
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /sync/work-plans
   */
  router.post("/sync/work-plans", async (req: any, res: any) => {
    const { id, agentId, conversationId, title, status, createdAt, updatedAt } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "import_work_plan", [
        id,
        agentId,
        conversationId || "",
        title,
        status || "active",
        BigInt(createdAt || 0),
        BigInt(updatedAt || 0)
      ], token);
      res.status(201).json({ status: "synced" });
    } catch (err: any) {
      console.error(`[persistence] import_work_plan failed:`, err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /sync/work-items
   */
  router.post("/sync/work-items", async (req: any, res: any) => {
    const { id, planId, title, status, ordinal, notes, filesChanged, createdAt, updatedAt } = req.body;
    try {
      await callReducer(spacetimedbUrl, spacetimedbModuleName, "import_work_item", [
        id,
        planId,
        title || "",
        status || "new",
        ordinal || 0,
        typeof notes === "string" ? notes : JSON.stringify(notes || []),
        typeof filesChanged === "string" ? filesChanged : JSON.stringify(filesChanged || []),
        BigInt(createdAt || 0),
        BigInt(updatedAt || 0)
      ]);
      res.status(201).json({ status: "synced" });
    } catch (err: any) {
      console.error(`[persistence] import_work_item failed:`, err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /spacetimedb/tables
   */
  router.get("/spacetimedb/tables", async (req: any, res: any) => {
    try {
      const data = await sqlQuery(spacetimedbUrl, spacetimedbModuleName, "SELECT * FROM system_table", token);
      res.json(data);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /spacetimedb/reducers
   */
  router.get("/spacetimedb/reducers", async (req: any, res: any) => {
    try {
      const data = await sqlQuery(spacetimedbUrl, spacetimedbModuleName, "SELECT * FROM system_reducer", token);
      res.json(data);
    } catch (err: any) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
