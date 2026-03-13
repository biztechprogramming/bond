import { Router, Request, Response } from "express";
import crypto from "node:crypto";
import { ulid } from "ulid";
import type { EventBus } from "./events/index.js";
import type { GatewayEvent } from "./events/index.js";

export interface WebhookRouterOptions {
  onMainMerge?: () => void;
  eventBus?: EventBus;
  onPush?: (repo: string, branch: string, actor?: string) => void;
}

/**
 * Extract "owner/repo" from a GitHub payload's repository object.
 */
function extractRepo(payload: any): string {
  return payload?.repository?.full_name ?? "";
}

/**
 * Extract branch name from a GitHub payload ref (e.g., "refs/heads/feature/foo" → "feature/foo").
 * Returns undefined for non-branch refs (tags, etc.).
 */
function extractBranch(payload: any, eventType: string): string | undefined {
  if (eventType === "push") {
    const ref: string = payload?.ref ?? "";
    if (ref.startsWith("refs/heads/")) return ref.slice("refs/heads/".length);
    return undefined;
  }
  if (eventType === "pull_request") {
    return payload?.pull_request?.head?.ref;
  }
  if (eventType === "check_run" || eventType === "check_suite") {
    return payload?.check_run?.check_suite?.head_branch
      ?? payload?.check_suite?.head_branch;
  }
  return undefined;
}

/**
 * Extract the actor (GitHub username) from a payload.
 */
function extractActor(payload: any): string | undefined {
  return payload?.sender?.login ?? payload?.pusher?.name ?? undefined;
}

/**
 * Normalize a raw GitHub webhook payload into a GatewayEvent.
 */
function normalizeGitHubEvent(
  eventType: string,
  payload: any,
  deliveryId: string,
): GatewayEvent {
  return {
    id: deliveryId || ulid(),
    source: "github",
    type: eventType,
    repo: extractRepo(payload),
    branch: extractBranch(payload, eventType),
    actor: extractActor(payload),
    payload,
    timestamp: Date.now(),
  };
}

export function createWebhookRouter(opts: WebhookRouterOptions = {}): Router {
  const router = Router();

  router.post("/github", (req: Request, res: Response) => {
    const secret = process.env.GITHUB_WEBHOOK_SECRET;
    if (secret) {
      const sig = req.headers["x-hub-signature-256"] as string | undefined;
      const body = (req as any).rawBody as Buffer | undefined;
      if (!body) {
        return res.status(400).json({ error: "No raw body" });
      }
      const expected =
        "sha256=" +
        crypto.createHmac("sha256", secret).update(body).digest("hex");
      if (!sig || sig !== expected) {
        return res.status(401).json({ error: "Invalid signature" });
      }
    }

    const event = req.headers["x-github-event"] as string;
    const deliveryId = req.headers["x-github-delivery"] as string ?? ulid();
    const payload = req.body;

    // Emit ALL events to the EventBus (if provided)
    if (event && opts.eventBus) {
      const gatewayEvent = normalizeGitHubEvent(event, payload, deliveryId);
      opts.eventBus.emit(gatewayEvent);

      // Notify push events via callback
      if (event === "push" && gatewayEvent.branch && opts.onPush) {
        opts.onPush(gatewayEvent.repo, gatewayEvent.branch, gatewayEvent.actor);
      }
    }

    // Backward-compatible: notify workers on push to main
    if (event === "push" && payload?.ref === "refs/heads/main") {
      console.log("[webhook] main branch updated — notifying workers");
      opts.onMainMerge?.();
    }

    res.json({ ok: true });
  });

  return router;
}
