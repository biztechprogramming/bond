/**
 * Conversations API — backed by SpacetimeDB.
 *
 * Replaces the Python backend's /api/v1/conversations endpoints.
 * Reads use SpacetimeDB's HTTP SQL API, writes use reducer calls.
 */

import { Router } from "express";
import { ulid } from "ulid";
import type { GatewayConfig } from "../config/index.js";
import { callReducer, sqlQuery } from "../spacetimedb/client.js";

// ── Router ──

export function createConversationsRouter(config: GatewayConfig) {
  const router = Router();
  const { spacetimedbUrl: url, spacetimedbModuleName: mod } = config;

  // Helper: build agent name lookup map
  async function agentNameMap(): Promise<Record<string, string>> {
    try {
      const agents = await sqlQuery(url, mod, "SELECT id, display_name FROM agents");
      const map: Record<string, string> = {};
      for (const a of agents) map[a.id] = a.display_name;
      return map;
    } catch {
      return {};
    }
  }

  /**
   * GET /api/v1/conversations
   */
  router.get("/conversations", async (_req: any, res: any) => {
    try {
      const rows = await sqlQuery(url, mod, "SELECT * FROM conversations");
      const agents = await agentNameMap();

      // Sort by updated_at descending
      rows.sort((a: any, b: any) => Number(b.updated_at) - Number(a.updated_at));

      const conversations = rows.map((r: any) => ({
        id: r.id,
        agent_id: r.agent_id,
        agent_name: agents[r.agent_id] || null,
        channel: r.channel,
        title: r.title || null,
        is_active: r.is_active,
        message_count: r.message_count,
        created_at: new Date(Number(r.created_at)).toISOString(),
        updated_at: new Date(Number(r.updated_at)).toISOString(),
      }));
      res.json(conversations);
    } catch (err: any) {
      console.error("[conversations] list failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /api/v1/conversations
   */
  router.post("/conversations", async (req: any, res: any) => {
    const { agent_id, channel = "webchat", title } = req.body;
    const id = ulid();

    try {
      // If no agent_id, find default
      let agentId = agent_id;
      if (!agentId) {
        const agents = await sqlQuery(url, mod, "SELECT id FROM agents WHERE is_default = true");
        if (agents.length === 0) {
          return res.status(500).json({ error: "No default agent configured" });
        }
        agentId = agents[0].id;
      }

      await callReducer(url, mod, "create_conversation", [id, agentId, channel, title || ""]);

      // Mirror to backend SQLite so history/switch_conversation lookups work
      try {
        await fetch(`${config.backendUrl}/api/v1/conversations`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id, agent_id: agentId, channel, title: title ?? null }),
        });
      } catch (syncErr: any) {
        console.warn("[conversations] backend sync failed (non-fatal):", syncErr.message);
      }

      // Return the created conversation
      const rows = await sqlQuery(url, mod, `SELECT * FROM conversations WHERE id = '${id}'`);
      if (rows.length === 0) {
        return res.status(201).json({ id, agent_id: agentId, channel, title });
      }
      const c = rows[0];
      res.status(201).json({
        id: c.id,
        agent_id: c.agent_id,
        channel: c.channel,
        title: c.title || null,
        is_active: c.is_active,
        message_count: c.message_count,
        created_at: new Date(Number(c.created_at)).toISOString(),
        updated_at: new Date(Number(c.updated_at)).toISOString(),
      });
    } catch (err: any) {
      console.error("[conversations] create failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /api/v1/conversations/:id
   */
  router.get("/conversations/:id", async (req: any, res: any) => {
    const { id } = req.params;
    try {
      const convs = await sqlQuery(url, mod, `SELECT * FROM conversations WHERE id = '${id}'`);
      if (convs.length === 0) {
        return res.status(404).json({ error: "Conversation not found" });
      }
      const c = convs[0];
      const agents = await agentNameMap();

      const msgs = await sqlQuery(
        url,
        mod,
        `SELECT * FROM conversation_messages WHERE conversation_id = '${id}'`
      );
      msgs.sort((a: any, b: any) => Number(a.created_at) - Number(b.created_at));

      res.json({
        id: c.id,
        agent_id: c.agent_id,
        agent_name: agents[c.agent_id] || null,
        channel: c.channel,
        title: c.title || null,
        is_active: c.is_active,
        message_count: c.message_count,
        created_at: new Date(Number(c.created_at)).toISOString(),
        updated_at: new Date(Number(c.updated_at)).toISOString(),
        messages: msgs.map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          tool_calls: m.tool_calls || null,
          tool_call_id: m.tool_call_id || null,
          token_count: m.token_count || null,
          created_at: new Date(Number(m.created_at)).toISOString(),
        })),
      });
    } catch (err: any) {
      console.error("[conversations] get failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /api/v1/conversations/:id/messages
   */
  router.get("/conversations/:id/messages", async (req: any, res: any) => {
    const { id } = req.params;
    const limit = Math.min(parseInt(req.query.limit || "100", 10), 1000);
    const offset = parseInt(req.query.offset || "0", 10);

    try {
      // Verify conversation exists
      const convs = await sqlQuery(url, mod, `SELECT id FROM conversations WHERE id = '${id}'`);
      if (convs.length === 0) {
        return res.status(404).json({ error: "Conversation not found" });
      }

      const msgs = await sqlQuery(
        url,
        mod,
        `SELECT * FROM conversation_messages WHERE conversation_id = '${id}'`
      );
      msgs.sort((a: any, b: any) => Number(a.created_at) - Number(b.created_at));
      const sliced = msgs.slice(offset, offset + limit);

      res.json(
        sliced.map((m: any) => ({
          id: m.id,
          role: m.role,
          content: m.content,
          tool_calls: m.tool_calls || null,
          tool_call_id: m.tool_call_id || null,
          token_count: m.token_count || null,
          created_at: new Date(Number(m.created_at)).toISOString(),
        }))
      );
    } catch (err: any) {
      console.error("[conversations] messages failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * PUT /api/v1/conversations/:id
   */
  router.put("/conversations/:id", async (req: any, res: any) => {
    const { id } = req.params;
    const { title } = req.body;

    try {
      await callReducer(url, mod, "update_conversation", [id, title]);
      const rows = await sqlQuery(url, mod, `SELECT * FROM conversations WHERE id = '${id}'`);
      if (rows.length === 0) {
        return res.status(404).json({ error: "Conversation not found" });
      }
      const c = rows[0];
      res.json({
        id: c.id,
        agent_id: c.agent_id,
        channel: c.channel,
        title: c.title || null,
        is_active: c.is_active,
        message_count: c.message_count,
        created_at: new Date(Number(c.created_at)).toISOString(),
        updated_at: new Date(Number(c.updated_at)).toISOString(),
      });
    } catch (err: any) {
      console.error("[conversations] update failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * DELETE /api/v1/conversations/:id
   */
  router.delete("/conversations/:id", async (req: any, res: any) => {
    const { id } = req.params;
    try {
      await callReducer(url, mod, "delete_conversation", [id]);
      // Mirror delete to backend SQLite
      try {
        await fetch(`${config.backendUrl}/api/v1/conversations/${id}`, { method: "DELETE" });
      } catch (syncErr: any) {
        console.warn("[conversations] backend delete sync failed (non-fatal):", syncErr.message);
      }
      res.json({ status: "deleted", conversation_id: id });
    } catch (err: any) {
      console.error("[conversations] delete failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /api/v1/conversations/:id/messages
   */
  router.post("/conversations/:id/messages", async (req: any, res: any) => {
    const { id: conversationId } = req.params;
    const { content, role = "user" } = req.body;
    const msgId = ulid();

    try {
      // Verify conversation exists
      const convs = await sqlQuery(url, mod, `SELECT * FROM conversations WHERE id = '${conversationId}'`);
      if (convs.length === 0) {
        return res.status(404).json({ error: "Conversation not found" });
      }

      const status = role === "assistant" ? "delivered" : "queued";

      // Auto-title from first user message if untitled
      if (role === "user" && !convs[0].title) {
        let autoTitle = content.trim().substring(0, 80);
        if (content.trim().length > 80) {
          const lastSpace = autoTitle.lastIndexOf(" ");
          if (lastSpace > 0) autoTitle = autoTitle.substring(0, lastSpace);
          autoTitle += "...";
        }
        await callReducer(url, mod, "update_conversation", [conversationId, autoTitle]);
      }

      await callReducer(url, mod, "add_conversation_message", [
        msgId,
        conversationId,
        role,
        content,
        "", // tool_calls
        "", // tool_call_id
        0,  // token_count
        status,
      ]);

      if (status === "queued") {
        // Count queued messages — SpacetimeDB SQL doesn't support AND, so filter in JS
        const allMsgs = await sqlQuery(
          url,
          mod,
          `SELECT * FROM conversation_messages WHERE conversation_id = '${conversationId}'`
        );
        const queuedMsgs = allMsgs.filter((m: any) => m.status === "queued");
        res.status(201).json({
          message_id: msgId,
          status: "queued",
          queue_position: queuedMsgs.length,
        });
      } else {
        res.status(201).json({
          message_id: msgId,
          conversation_id: conversationId,
        });
      }
    } catch (err: any) {
      console.error("[conversations] add message failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * DELETE /api/v1/conversations/:id/messages/:messageId
   */
  router.delete("/conversations/:id/messages/:messageId", async (req: any, res: any) => {
    const { id: conversationId, messageId } = req.params;
    try {
      await callReducer(url, mod, "delete_conversation_message", [messageId, conversationId]);
      res.json({ status: "deleted", message_id: messageId });
    } catch (err: any) {
      console.error("[conversations] delete message failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
