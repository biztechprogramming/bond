/**
 * Plans API — backed by SpacetimeDB.
 *
 * Provides CRUD for work_plans and work_items.
 * The worker reads via GET; writes go through SpacetimeDB reducers.
 */

import { Router } from "express";
import { ulid } from "ulid";
import type { GatewayConfig } from "../config/index.js";

// ── SpacetimeDB helpers (duplicated from conversations/router.ts — consider extracting) ──

async function callReducer(
  baseUrl: string,
  module: string,
  reducer: string,
  args: (string | number | boolean)[]
): Promise<void> {
  const url = `${baseUrl}/v1/database/${module}/call/${reducer}`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`SpacetimeDB ${reducer} failed (${res.status}): ${body}`);
  }
}

async function sqlQuery(
  baseUrl: string,
  module: string,
  sql: string
): Promise<any[]> {
  const url = `${baseUrl}/v1/database/${module}/sql`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: sql,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`SpacetimeDB SQL failed (${res.status}): ${body}`);
  }
  const data = await res.json();
  if (!data || !Array.isArray(data) || data.length === 0) return [];
  const resultSet = data[0];
  if (!resultSet.rows || !resultSet.schema) return [];
  const columns = resultSet.schema.elements.map((e: any) => e.name?.some || e.name);
  return resultSet.rows.map((row: any[]) => {
    const obj: any = {};
    columns.forEach((col: string, i: number) => {
      obj[col] = row[i];
    });
    return obj;
  });
}

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
      const { agent_id, status, limit } = req.query;
      let plans = await sqlQuery(url, mod, "SELECT * FROM work_plans");

      if (agent_id) plans = plans.filter((p: any) => p.agent_id === agent_id);
      if (status) plans = plans.filter((p: any) => p.status === status);

      // Sort by updated_at descending
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
    const { title, ordinal } = req.body;
    if (!title) return res.status(400).json({ error: "title is required" });

    const itemId = ulid();
    try {
      let ord = ordinal;
      if (ord === undefined) {
        const existing = await sqlQuery(url, mod, `SELECT * FROM work_items WHERE plan_id = '${planId}'`);
        ord = existing.length;
      }
      // add_work_item: {id, planId, title, ordinal}
      await callReducer(url, mod, "add_work_item", [itemId, planId, title, ord]);
      res.status(201).json({
        item_id: itemId,
        id: itemId,
        plan_id: planId,
        title,
        status: "new",
        ordinal: ord,
      });
    } catch (err: any) {
      console.error("[plans] add item failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

  /**
   * PUT /plans/:planId/items/:itemId
   * Update a work item (status, notes, files_changed).
   * Single reducer call: update_work_item {id, status, notes?, filesChanged?}
   */
  router.put("/plans/:planId/items/:itemId", async (req: any, res: any) => {
    const { itemId } = req.params;
    const { status, notes, files_changed } = req.body;

    if (status === undefined && notes === undefined && files_changed === undefined) {
      return res.status(400).json({ error: "Provide at least one of: status, notes, files_changed" });
    }

    // update_work_item takes: {id, status, notes?, filesChanged?}
    // status is required by the reducer — fetch current if not provided
    let resolvedStatus = status;
    if (resolvedStatus === undefined) {
      const rows = await sqlQuery(url, mod, `SELECT * FROM work_items WHERE id = '${itemId}'`);
      if (rows.length === 0) return res.status(404).json({ error: "Item not found" });
      resolvedStatus = rows[0].status;
    }

    // update_work_item positional args: [id, status, notes?, filesChanged?]
    // Only include optional args if provided — don't pass nulls for missing ones
    const reducerArgs: string[] = [itemId, resolvedStatus];
    if (notes !== undefined || files_changed !== undefined) {
      const notesJson = notes !== undefined
        ? JSON.stringify(Array.isArray(notes) ? notes : [{ text: String(notes) }])
        : undefined;
      const filesJson = files_changed !== undefined ? JSON.stringify(files_changed) : undefined;
      // If either optional arg is needed, we must provide both in order
      // Use existing values from SpacetimeDB when one is missing
      if (notesJson !== undefined) reducerArgs.push(notesJson);
      if (filesJson !== undefined) {
        if (notesJson === undefined) {
          // Need to fetch current notes to preserve them
          const rows = await sqlQuery(url, mod, `SELECT * FROM work_items WHERE id = '${itemId}'`);
          reducerArgs.push(rows[0]?.notes ?? "[]");
        }
        reducerArgs.push(filesJson);
      }
    }

    try {
      await callReducer(url, mod, "update_work_item", reducerArgs);
      res.json({ item_id: itemId, updated: true });
    } catch (err: any) {
      console.error("[plans] update item failed:", err.message);
      res.status(500).json({ error: err.message });
    }
  });

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

  return router;
}
