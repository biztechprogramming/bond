import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import express from "express";
import { createServer, type Server } from "http";
import crypto from "node:crypto";
import { createWebhookRouter } from "../webhooks.js";
import { EventBus } from "../events/event-bus.js";

// ---------- helpers ----------

function buildApp(opts: { onMainMerge?: () => void; secret?: string; eventBus?: EventBus } = {}) {
  const app = express();

  // Raw body capture (mirrors server.ts middleware)
  app.use("/webhooks/github", (req: any, _res: any, next: any) => {
    let data: Buffer[] = [];
    req.on("data", (chunk: Buffer) => data.push(chunk));
    req.on("end", () => {
      (req as any).rawBody = Buffer.concat(data);
      try {
        req.body = JSON.parse((req as any).rawBody.toString());
      } catch {
        // noop
      }
      next();
    });
  });

  if (opts.secret) {
    process.env.GITHUB_WEBHOOK_SECRET = opts.secret;
  } else {
    delete process.env.GITHUB_WEBHOOK_SECRET;
  }

  const router = createWebhookRouter({ onMainMerge: opts.onMainMerge, eventBus: opts.eventBus });
  app.use("/webhooks", router);
  return app;
}

async function listen(app: ReturnType<typeof express>): Promise<{
  server: Server;
  port: number;
}> {
  return new Promise((resolve) => {
    const server = createServer(app);
    server.listen(0, "127.0.0.1", () => {
      const addr = server.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      resolve({ server, port });
    });
  });
}

async function post(
  port: number,
  path: string,
  body: any,
  headers: Record<string, string> = {}
): Promise<{ status: number; body: any }> {
  const payload = JSON.stringify(body);
  const res = await fetch(`http://127.0.0.1:${port}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: payload,
  });
  const json = await res.json();
  return { status: res.status, body: json };
}

function sign(secret: string, payload: string): string {
  return (
    "sha256=" + crypto.createHmac("sha256", secret).update(payload).digest("hex")
  );
}

// ---------- tests ----------

describe("webhooks", () => {
  let server: Server | undefined;

  beforeEach(() => {
    delete process.env.GITHUB_WEBHOOK_SECRET;
  });

  afterEach(() => {
    server?.close();
    server = undefined;
    delete process.env.GITHUB_WEBHOOK_SECRET;
  });

  it("valid signature triggers onMainMerge", async () => {
    const onMainMerge = vi.fn();
    const secret = "test-secret-123";
    const app = buildApp({ onMainMerge, secret });
    const s = await listen(app);
    server = s.server;

    const payload = JSON.stringify({ ref: "refs/heads/main" });
    const sig = sign(secret, payload);

    const res = await post(s.port, "/webhooks/github", JSON.parse(payload), {
      "x-github-event": "push",
      "x-hub-signature-256": sig,
    });

    expect(res.status).toBe(200);
    expect(res.body.ok).toBe(true);
    expect(onMainMerge).toHaveBeenCalledOnce();
  });

  it("invalid signature returns 401", async () => {
    const onMainMerge = vi.fn();
    const secret = "real-secret";
    const app = buildApp({ onMainMerge, secret });
    const s = await listen(app);
    server = s.server;

    const res = await post(
      s.port,
      "/webhooks/github",
      { ref: "refs/heads/main" },
      {
        "x-github-event": "push",
        "x-hub-signature-256": "sha256=invalid",
      }
    );

    expect(res.status).toBe(401);
    expect(onMainMerge).not.toHaveBeenCalled();
  });

  it("push to non-main ref does not trigger onMainMerge", async () => {
    const onMainMerge = vi.fn();
    const app = buildApp({ onMainMerge });
    const s = await listen(app);
    server = s.server;

    const res = await post(
      s.port,
      "/webhooks/github",
      { ref: "refs/heads/feat/something" },
      { "x-github-event": "push" }
    );

    expect(res.status).toBe(200);
    expect(onMainMerge).not.toHaveBeenCalled();
  });

  it("missing event does nothing", async () => {
    const onMainMerge = vi.fn();
    const app = buildApp({ onMainMerge });
    const s = await listen(app);
    server = s.server;

    const res = await post(s.port, "/webhooks/github", {
      ref: "refs/heads/main",
    });

    expect(res.status).toBe(200);
    expect(onMainMerge).not.toHaveBeenCalled();
  });

  it("emits to eventBus when provided", async () => {
    const eventBus = new EventBus();
    const handler = vi.fn();
    eventBus.onMatch(handler);
    // Subscribe to all push events
    eventBus.subscribe({
      conversationId: "conv-1",
      agentId: "agent-1",
      filter: { source: "github", type: "push" },
      context: "test",
      expiresAt: Date.now() + 3600_000,
      maxDeliveries: 10,
    });

    const app = buildApp({ eventBus });
    const s = await listen(app);
    server = s.server;

    const res = await post(
      s.port,
      "/webhooks/github",
      { ref: "refs/heads/feature/x", repository: { full_name: "org/repo" }, sender: { login: "alice" } },
      { "x-github-event": "push" }
    );

    expect(res.status).toBe(200);
    expect(handler).toHaveBeenCalledOnce();
    const [event] = handler.mock.calls[0];
    expect(event.source).toBe("github");
    expect(event.type).toBe("push");
    expect(event.repo).toBe("org/repo");
    expect(event.branch).toBe("feature/x");
    expect(event.actor).toBe("alice");
  });

  it("normalizes pull_request event with branch from PR head ref", async () => {
    const eventBus = new EventBus();
    const emitted: any[] = [];
    const origEmit = eventBus.emit.bind(eventBus);
    vi.spyOn(eventBus, "emit").mockImplementation((ev) => {
      emitted.push(ev);
      origEmit(ev);
    });

    const app = buildApp({ eventBus });
    const s = await listen(app);
    server = s.server;

    const payload = {
      action: "opened",
      pull_request: { head: { ref: "feature/pr-branch" }, number: 42 },
      repository: { full_name: "org/repo" },
      sender: { login: "bob" },
    };

    await post(s.port, "/webhooks/github", payload, { "x-github-event": "pull_request" });

    expect(emitted).toHaveLength(1);
    expect(emitted[0].type).toBe("pull_request");
    expect(emitted[0].branch).toBe("feature/pr-branch");
    expect(emitted[0].actor).toBe("bob");
  });

  it("does not emit to eventBus when no eventBus provided", async () => {
    // Just verifies the old behavior still works without eventBus
    const app = buildApp({});
    const s = await listen(app);
    server = s.server;

    const res = await post(
      s.port,
      "/webhooks/github",
      { ref: "refs/heads/feature/x" },
      { "x-github-event": "push" }
    );

    expect(res.status).toBe(200);
  });
});
