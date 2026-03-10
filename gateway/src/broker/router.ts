/**
 * Permission Broker — Express router.
 */

import { Router } from "express";
import type { Request, Response, NextFunction } from "express";
import type { AgentTokenPayload, BrokerConfig } from "./types.js";
import { initTokens, issueToken, validateToken } from "./tokens.js";
import { AuditLogger } from "./audit.js";
import { PolicyEngine } from "./policy.js";
import { executeCommand } from "./executor.js";

declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace Express {
    interface Request {
      agentToken?: AgentTokenPayload;
    }
  }
}

function authMiddleware(req: Request, res: Response, next: NextFunction): void {
  const auth = req.headers.authorization;
  if (!auth || !auth.startsWith("Bearer ")) {
    res.status(401).json({ error: "Missing or invalid Authorization header" });
    return;
  }

  const token = auth.slice(7);
  const payload = validateToken(token);
  if (!payload) {
    res.status(401).json({ error: "Invalid or expired token" });
    return;
  }

  req.agentToken = payload;
  next();
}

export function createBrokerRouter(config: BrokerConfig): Router {
  initTokens(config.dataDir);

  const router = Router();
  const audit = new AuditLogger(config.dataDir);
  const policy = new PolicyEngine();
  const startTime = Date.now();

  // Health check — no auth
  router.get("/health", (_req: Request, res: Response) => {
    res.json({
      status: "ok",
      service: "permission-broker",
      uptime_ms: Date.now() - startTime,
    });
  });

  // All other routes require auth
  router.use(authMiddleware);

  // POST /exec
  router.post("/exec", async (req: Request, res: Response) => {
    const { command, cwd, timeout, env } = req.body || {};
    const agent = req.agentToken!;

    if (!command || typeof command !== "string") {
      res.status(400).json({ error: "command is required" });
      return;
    }

    const decision = policy.evaluate(command, cwd, agent.sub, agent.sid);

    // Phase 1: treat "prompt" as deny
    if (decision.decision === "prompt") {
      decision.decision = "deny";
      decision.reason = "Requires user approval (not yet implemented)";
    }

    if (decision.decision === "deny") {
      audit.log({
        timestamp: new Date().toISOString(),
        agent_id: agent.sub,
        session_id: agent.sid,
        command,
        cwd,
        decision: "deny",
        policy_rule: decision.source,
      });

      res.json({
        status: "denied",
        decision: "deny",
        reason: decision.reason,
        policy_rule: decision.source,
      });
      return;
    }

    // Execute
    const result = await executeCommand(command, { cwd, timeout, env });

    audit.log({
      timestamp: new Date().toISOString(),
      agent_id: agent.sub,
      session_id: agent.sid,
      command,
      cwd,
      decision: "allow",
      policy_rule: decision.source,
      exit_code: result.exit_code,
      stdout_len: result.stdout.length,
      stderr_len: result.stderr.length,
      duration_ms: result.duration_ms,
    });

    res.json({
      status: "ok",
      decision: "allow",
      exit_code: result.exit_code,
      stdout: result.stdout,
      stderr: result.stderr,
      duration_ms: result.duration_ms,
    });
  });

  // POST /token/renew
  router.post("/token/renew", (req: Request, res: Response) => {
    const agent = req.agentToken!;
    const newToken = issueToken(agent.sub, agent.sid);
    res.json({ token: newToken });
  });

  return router;
}
