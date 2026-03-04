import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import express from "express";
import { createServer, type Server } from "http";
import crypto from "node:crypto";
import { createWebhookRouter } from "../webhooks.js";

// ---------- helpers ----------

function buildApp(opts: { onMainMerge?: () => void; secret?: string } = {}) {
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

  const router = createWebhookRouter({ onMainMerge: opts.onMainMerge });
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
});
