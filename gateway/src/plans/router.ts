/**
 * Plans API — backed by SpacetimeDB.
 *
 * Provides CRUD for work_plans and work_items.
 * The worker reads via GET; writes go through SpacetimeDB reducers.
 */

import { Router } from "express";
import { ulid } from "ulid";
import type { GatewayConfig } from "../config/index.js";
import { callReducer, sqlQuery, encodeOption } from "../spacetimedb/client.js";

// ── Helpers ──

function formatPlan(plan: any, items: any[]): object {
  const sortedItems = [...items].sort((a, b) => (a.ordinal ?? 0) - (b.ordinal ?? 0));
  return {
    id: plan.id,
    agent_id: plan.agent_id,
    conversation_id: plan.conversation_id || null,
    title: plan.title,
    status: plan.status,
    created_at: new Date(Number(plan.created_at)).toISOString(),
    updated_at: new Date(Number(plan.updated_at)).toISOString(),
    items: sortedItems.map((i) => ({
      id: i.id,
      plan_id: i.plan_id,
      title: i.title,
      status: i.status,
      ordinal: i.ordinal ?? 0,
      notes: safeParseJson(i.notes, []),
      files_changed: safeParseJson(i.files_changed, []),
      created_at: new Date(Number(i.created_at)).toISOString(),
      updated_at: new Date(Number(i.updated_at)).toISOString(),
    })),
  };
}

function safeParseJson(val: any, fallback: any): any {
  if (val === null || val === undefined || val === "") return fallback;
  if (typeof val !== "string") return val;
  try {
    return JSON.parse(val);
  } catch {
    return fallback;
  }
}

// ── Router ──

export function createPlansRouter(config: GatewayConfig) {
  const router = Router();
  const { spacetimedbUrl: url, spacetimedbModuleName: mod } = config;

  /**
   * GET /plans?agent_id=X&status=active&limit=1
   * List plans, optionally filtered by agent_id and/or status.
   */
  router.get("/plans", async (req: any, res: any) => {
    try {
      const { agent_id, status, limit, conversation_id } = req.query;
      let plans = await sqlQuery(url, mod, "SELECT * FROM work_plans");

      if (agent_id) plans = plans.filter((p: any) => p.agent_id === agent_id);
      if (status) plans = plans.filter((p: any) => p.status === status);
      if (conversation_id) plans = plans.filter((p: any) => p.conversation_id === conversation_id);

      plans.sort((a: any, b: any) => Number(b.updated_at) - Number(a.updated_at));

      if (limit) plans = plans.slice(0, parseInt(limit as string, 10));

      res.json(
        plans.map((p: any) => ({
          id: p.id,
          agent_id: p.agent_id,
          conversation_id: p.conversation_id || null,
          title: p.title,
          status: p.status,
          created_at: new Date(Number(p.created_at)).toISOString(),
          updated_at: new Date(Number(p.updated_at)).toISOString(),
        }))
      );
    } catch (err: any) {
      console.error("[plans] list failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * GET /plans/:planId
   * Get a single plan with all its items.
   */
  router.get("/plans/:planId", async (req: any, res: any) => {
    const { planId } = req.params;
    try {
      const plans = await sqlQuery(url, mod, `SELECT * FROM work_plans WHERE id = '${planId}'`);
      if (plans.length === 0) {
        return res.status(404).json({ error: "Plan not found" });
      }
      const items = await sqlQuery(url, mod, `SELECT * FROM work_items WHERE plan_id = '${planId}'`);
      res.json(formatPlan(plans[0], items));
    } catch (err: any) {
      console.error("[plans] get failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /plans
   * Create a new plan.
   */
  router.post("/plans", async (req: any, res: any) => {
    const { title, agent_id, conversation_id } = req.body;
    if (!title) return res.status(400).json({ error: "title is required" });
    if (!agent_id) return res.status(400).json({ error: "agent_id is required" });

    const planId = ulid();
    try {
      await callReducer(url, mod, "create_work_plan", [
        planId,
        agent_id,
        conversation_id || "",
        title,
      ]);
      res.status(201).json({
        plan_id: planId,
        id: planId,
        title,
        agent_id,
        conversation_id: conversation_id || null,
        status: "active",
      });
    } catch (err: any) {
      console.error("[plans] create failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * POST /plans/:planId/items
   * Add an item to a plan.
   */
  router.post("/plans/:planId/items", async (req: any, res: any) => {
    const { planId } = req.params;
    const { title, ordinal, description = "" } = req.body;
    if (!title) return res.status(400).json({ error: "title is required" });

    const itemId = ulid();
    try {
      let ord = ordinal;
      if (ord === undefined) {
        const existing = await sqlQuery(url, mod, `SELECT * FROM work_items WHERE plan_id = '${planId}'`);
        ord = existing.length;
      }
      // add_work_item: {id, planId, title, ordinal, description}
      await callReducer(url, mod, "add_work_item", [itemId, planId, title, ord, description]);
      res.status(201).json({
        item_id: itemId,
        id: itemId,
        plan_id: planId,
        title,
        description,
        status: "new",
        ordinal: ord,
      });
    } catch (err: any) {
      console.error("[plans] add item failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * PUT /items/:itemId   (flat — no plan_id needed)
   * Alias for the nested route below. The agent often loses plan_id from
   * context by the time it needs to update an item.
   */
  router.put("/items/:itemId", async (req: any, res: any) => {
    req.params.planId = "_"; // planId not used by the handler
    return updateItemHandler(req, res);
  });

  /**
   * PUT /plans/:planId/items/:itemId
   * Update a work item (status, notes, files_changed).
   * Single reducer call: update_work_item {id, status, notes?, filesChanged?}
   */
  router.put("/plans/:planId/items/:itemId", updateItemHandler);

  async function updateItemHandler(req: any, res: any) {
    const { itemId } = req.params;
    const { status, notes, files_changed, title, description } = req.body;

    if (status === undefined && notes === undefined && files_changed === undefined && title === undefined && description === undefined) {
      return res.status(400).json({ error: "Provide at least one of: title, status, notes, files_changed, description" });
    }

    if (title !== undefined) {
      try {
        await callReducer(url, mod, "rename_work_item", [itemId, title]);
      } catch (err: any) {
        console.error("[plans] rename item failed:", err.message);
        return res.status(500).json({ error: err.message });
      }
      // If only title was requested, return early
      if (status === undefined && notes === undefined && files_changed === undefined && description === undefined) {
        return res.json({ item_id: itemId, updated: true });
      }
    }

    // update_work_item reducer always requires exactly 4 args: [id, status, notes, files_changed]
    // Fetch current row to fill in any fields not provided by the caller
    const currentRows = await sqlQuery(url, mod, `SELECT * FROM work_items WHERE id = '${itemId}'`);
    if (currentRows.length === 0) return res.status(404).json({ error: "Item not found" });
    const current = currentRows[0];

    const resolvedStatus = status ?? current.status;

    const resolvedNotes = notes !== undefined
      ? encodeOption(JSON.stringify(Array.isArray(notes) ? notes : [{ text: String(notes) }]))
      : encodeOption(current.notes ?? null);
    const resolvedFiles = files_changed !== undefined
      ? encodeOption(JSON.stringify(files_changed))
      : encodeOption(current.files_changed ?? null);
    const resolvedDescription = description !== undefined ? description : (current.description ?? "");

    // Reducer positional args: [id, status, notes, filesChanged, description]
    const reducerArgs = [itemId, resolvedStatus, resolvedNotes, resolvedFiles, encodeOption(resolvedDescription)];

    try {
      await callReducer(url, mod, "update_work_item", reducerArgs);
      res.json({ item_id: itemId, updated: true });
    } catch (err: any) {
      console.error("[plans] update item failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  }

  /**
   * POST /plans/:planId/complete
   * Mark a plan as complete (or failed/cancelled).
   */
  router.post("/plans/:planId/complete", async (req: any, res: any) => {
    const { planId } = req.params;
    const { status = "completed" } = req.body;
    try {
      // update_work_plan_status: {id, status}
      await callReducer(url, mod, "update_work_plan_status", [planId, status]);
      res.json({ plan_id: planId, status });
    } catch (err: any) {
      console.error("[plans] complete plan failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * DELETE /plans/:planId
   * Permanently delete a plan and all its items.
   */
  router.delete("/plans/:planId", async (req: any, res: any) => {
    const { planId } = req.params;
    try {
      await callReducer(url, mod, "delete_work_plan", [planId]);
      res.json({ plan_id: planId, deleted: true });
    } catch (err: any) {
      console.error("[plans] delete plan failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}
