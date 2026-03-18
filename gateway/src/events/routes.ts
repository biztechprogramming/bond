/**
 * Event subscription HTTP API routes.
 * Mounted at /api/v1/events in server.ts
 * See design doc 040-gateway-event-subscriptions.md §9
 */

import { Router, Request, Response } from "express";
import type { EventBus } from "./event-bus.js";

export function createEventsRouter(eventBus: EventBus): Router {
  const router = Router();

  // POST /api/v1/events/subscribe — create a subscription
  router.post("/subscribe", (req: Request, res: Response) => {
    const {
      filter,
      conversationId,
      agentId,
      context,
      maxDeliveries,
      expiresIn,
    } = req.body as {
      filter?: any;
      conversationId?: string;
      agentId?: string;
      context?: string;
      maxDeliveries?: number;
      expiresIn?: number;
    };

    if (!filter || !conversationId || !agentId || !context) {
      return res.status(400).json({
        error: "Required fields: filter, conversationId, agentId, context",
      });
    }

    const DEFAULT_TTL_MS = 2 * 60 * 60 * 1000; // 2 hours
    const expiresAt = Date.now() + (expiresIn ?? DEFAULT_TTL_MS);

    const id = eventBus.subscribe({
      filter,
      conversationId,
      agentId,
      context,
      expiresAt,
      maxDeliveries: maxDeliveries ?? 1,
    });

    res.status(201).json({ id });
  });

  // DELETE /api/v1/events/subscribe/:id — remove a subscription
  router.delete("/subscribe/:id", (req: Request, res: Response) => {
    const id = req.params.id as string;
    const removed = eventBus.unsubscribe(id);
    if (!removed) {
      return res.status(404).json({ error: "Subscription not found" });
    }
    res.json({ ok: true });
  });

  // GET /api/v1/events/subscriptions — list active subscriptions
  router.get("/subscriptions", (_req: Request, res: Response) => {
    const subs = eventBus.getSubscriptions();
    res.json({ subscriptions: subs });
  });

  // GET /api/v1/events/history — query event history
  router.get("/history", (req: Request, res: Response) => {
    const { repo, type, source, branch, actor, limit } = req.query as Record<string, string | undefined>;
    const filter: Record<string, string> = {};
    if (repo) filter.repo = repo;
    if (type) filter.type = type;
    if (source) filter.source = source;
    if (branch) filter.branch = branch;
    if (actor) filter.actor = actor;

    const maxLimit = Math.min(parseInt(limit ?? "100", 10), 1000);
    const events = eventBus.getHistory().query(filter, maxLimit);
    res.json({ events });
  });

  return router;
}
