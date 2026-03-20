/**
 * Permission Broker — Express router.
 */

import { Router } from "express";
import type { Request, Response, NextFunction } from "express";
import type { AgentTokenPayload, BrokerConfig } from "./types.js";
import { initTokens, issueToken, validateToken } from "./tokens.js";
import { AuditLogger } from "./audit.js";
import { PolicyEngine } from "./policy.js";
import { MCPPolicyEngine } from "./mcp-policy.js";
import { executeCommand } from "./executor.js";
import { handleDeploy } from "./deploy-handler.js";
import type { GatewayConfig } from "../config/index.js";

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

const BACKEND_BASE = process.env.BOND_BACKEND_URL
  || `${process.env.BOND_BACKEND_SCHEME || "http"}://${process.env.BOND_BACKEND_HOST || "127.0.0.1"}:${process.env.BOND_BACKEND_PORT || "18790"}`;

export function createBrokerRouter(config: BrokerConfig, gatewayConfig?: GatewayConfig): Router {
  initTokens(config.dataDir);

  const router = Router();
  const audit = new AuditLogger(config.dataDir);
  const policy = new PolicyEngine();
  const mcpPolicy = new MCPPolicyEngine();
  const startTime = Date.now();

  // Health check — no auth
  router.get("/health", (_req: Request, res: Response) => {
    res.json({
      status: "ok",
      service: "permission-broker",
      uptime_ms: Date.now() - startTime,
    });
  });

  // POST /token/issue — issue a new agent token (internal, no auth required)
  // Called by the Backend when spawning containers.
  router.post("/token/issue", (req: Request, res: Response) => {
    const { agent_id, session_id, ttl } = req.body || {};
    if (!agent_id || typeof agent_id !== "string") {
      res.status(400).json({ error: "agent_id is required" });
      return;
    }
    const token = issueToken(agent_id, session_id || "default", ttl || 86400);
    res.json({ token });
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

  // POST /deploy — deployment agent endpoint
  router.post("/deploy", async (req: Request, res: Response) => {
    const agent = req.agentToken!;

    if (!gatewayConfig) {
      res.status(503).json({ status: "error", reason: "Gateway config not available" });
      return;
    }

    const body = req.body || {};

    audit.log({
      timestamp: new Date().toISOString(),
      agent_id: agent.sub,
      session_id: agent.sid,
      command: `deploy:${body.action}:${body.script_id || ""}`,
      decision: "allow",
      policy_rule: "deploy-handler",
    });

    try {
      const result = await handleDeploy(gatewayConfig, agent, body);
      res.json(result);
    } catch (err: any) {
      console.error("[broker/deploy] Error:", err.message);
      res.status(500).json({ status: "error", reason: err.message });
    }
  });

  // ── MCP Proxy Endpoints (Design Doc 054) ──

  // GET /mcp/tools — list available MCP tools for an agent
  router.get("/mcp/tools", async (req: Request, res: Response) => {
    const agent = req.agentToken!;
    const agentId = (req.query.agent_id as string) || agent.sub;

    try {
      const backendUrl = `${BACKEND_BASE}/api/v1/mcp/proxy/tools?agent_id=${encodeURIComponent(agentId)}`;
      const resp = await fetch(backendUrl);
      if (!resp.ok) {
        const text = await resp.text();
        res.status(resp.status).json({ error: text });
        return;
      }

      const data = await resp.json() as { tools: Array<{ name: string }> };

      // Apply MCP policy filter
      const filteredTools = data.tools.filter((tool: { name: string }) => {
        const decision = mcpPolicy.evaluate(tool.name, agentId);
        return decision.decision === "allow";
      });

      audit.log({
        timestamp: new Date().toISOString(),
        agent_id: agentId,
        session_id: agent.sid,
        command: "mcp:list_tools",
        decision: "allow",
        policy_rule: "mcp-proxy",
      });

      res.json({ tools: filteredTools });
    } catch (err: any) {
      console.error("[broker/mcp/tools] Error:", err.message);
      res.status(502).json({ error: `Backend unreachable: ${err.message}` });
    }
  });

  // POST /mcp — execute an MCP tool call
  router.post("/mcp", async (req: Request, res: Response) => {
    const agent = req.agentToken!;
    const { tool_name, arguments: toolArgs, agent_id } = req.body || {};
    const effectiveAgentId = agent_id || agent.sub;

    if (!tool_name || typeof tool_name !== "string") {
      res.status(400).json({ error: "tool_name is required" });
      return;
    }

    // Evaluate MCP policy
    const policyDecision = mcpPolicy.evaluate(tool_name, effectiveAgentId, agent.sid);

    if (policyDecision.decision === "deny") {
      audit.log({
        timestamp: new Date().toISOString(),
        agent_id: effectiveAgentId,
        session_id: agent.sid,
        command: `mcp:${tool_name}`,
        decision: "deny",
        policy_rule: policyDecision.rule || "mcp-policy",
      });

      res.status(403).json({
        error: "MCP tool call denied by policy",
        reason: policyDecision.reason,
        rule: policyDecision.rule,
      });
      return;
    }

    // Forward to backend
    try {
      const backendUrl = `${BACKEND_BASE}/api/v1/mcp/proxy/call`;
      const resp = await fetch(backendUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tool_name,
          arguments: toolArgs || {},
          agent_id: effectiveAgentId,
        }),
      });

      const data = await resp.json();

      audit.log({
        timestamp: new Date().toISOString(),
        agent_id: effectiveAgentId,
        session_id: agent.sid,
        command: `mcp:${tool_name}`,
        decision: "allow",
        policy_rule: policyDecision.rule || "mcp-policy",
      });

      res.status(resp.status).json(data);
    } catch (err: any) {
      console.error("[broker/mcp] Error:", err.message);
      res.status(502).json({ error: `Backend unreachable: ${err.message}` });
    }
  });

  return router;
}
