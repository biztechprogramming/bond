import { Router, Request, Response } from "express";
import crypto from "node:crypto";

export function createWebhookRouter(
  opts: { onMainMerge?: () => void } = {}
): Router {
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
    const payload = req.body;

    if (event === "push" && payload?.ref === "refs/heads/main") {
      console.log("[webhook] main branch updated — notifying workers");
      opts.onMainMerge?.();
    }

    res.json({ ok: true });
  });

  return router;
}
