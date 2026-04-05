/**
 * Bond Gateway — WebSocket server.
 *
 * Handles WebSocket connections from the frontend webchat,
 * routes messages to the Python backend, and streams responses back.
 */

import "dotenv/config";
import { WebSocketServer } from "ws";
import { createServer } from "http";
import { homedir } from "node:os";
import { join } from "node:path";
import express from "express";
import { ulid } from "ulid";
import type { GatewayConfig } from "./config/index.js";
import { SessionManager } from "./sessions/index.js";
import { BackendClient } from "./backend/index.js";
import { WebChatChannel } from "./channels/index.js";
import { createPersistenceRouter } from "./persistence/index.js";
import { createConversationsRouter } from "./conversations/index.js";
import { createPlansRouter } from "./plans/index.js";
import { createWebhookRouter } from "./webhooks.js";
import { BranchManager } from "./branches/manager.js";
import { WebhookRegistrar } from "./webhooks/registrar.js";
import { createBrokerRouter } from "./broker/router.js";
import { createDeploymentsRouter } from "./deployments/router.js";
import { createBackupsRouter } from "./backups/router.js";
import { initBackupScheduler } from "./backups/scheduler.js";
import { initSessionTokens } from "./deployments/session-tokens.js";
import { EventBus, EventHistory, CompletionDispatcher, createEventsRouter } from "./events/index.js";
import { ChannelManager } from "./channels/manager.js";
import { createChannelRouter } from "./channels/routes.js";
import { createSolidTimeRouter } from "./integrations/solidtime.js";
import {
  MessagePipeline,
  RateLimitHandler,
  AuthHandler,
  AllowListHandler,
  AgentResolver,
  ContextLoader,
  TurnExecutor,
  Persister,
  ResponseFanOut,
} from "./pipeline/index.js";
import type { AllowListProvider } from "./pipeline/index.js";
import { initSubscription } from "./spacetimedb/subscription.js";
import { CompletionHandler } from "./completion/handler.js";
import { initAgentNotifier, getAgentStartupMessage } from "./deployments/events.js";

export interface GatewayServer {
  close(): void;
}

export function startGatewayServer(config: GatewayConfig): GatewayServer {
  const sessionManager = new SessionManager();
  const backendClient = new BackendClient(config.backendUrl);
  backendClient.setApiKey(config.apiKey);
  const webchat = new WebChatChannel(sessionManager, backendClient);
  webchat.setConfig(config);

  // Event subscription system
  const eventHistory = new EventHistory();
  const eventBus = new EventBus(eventHistory);
  const completionDispatcher = new CompletionDispatcher(
    backendClient,
    (conversationId, msg) => (webchat as any).sendToConversation(conversationId, msg),
  );
  eventBus.onMatch((event, sub) => {
    completionDispatcher.dispatch(event, sub).catch((err) => {
      console.error("[events] CompletionDispatcher error:", err);
    });
  });

  // Broadcast push events to all connected webchat clients as toast notifications
  eventBus.getHistory(); // ensure history is initialized
  const originalEmit = eventBus.emit.bind(eventBus);
  eventBus.emit = (event) => {
    originalEmit(event);
    if (event.type === "push" && event.branch) {
      webchat.broadcast({
        type: "webhook_push" as any,
        content: JSON.stringify({
          repo: event.repo,
          branch: event.branch,
          actor: event.actor,
        }),
      });
    }
  };

  eventBus.startCleanup();

  // Wire deployment agent notifier — sends messages to deploy-* agents via backend
  initAgentNotifier(async (agentName: string, message: string) => {
    // Find agent ID from backend
    const agents = await backendClient.listAgents();
    const agent = agents.find((a) => a.name === agentName);
    if (!agent) {
      console.warn(`[deploy-notify] Agent '${agentName}' not found — skipping notification`);
      return;
    }

    // Find or create a conversation for this deploy agent
    let conversationId = await backendClient.findActiveConversation(agent.id);
    if (!conversationId) {
      conversationId = ulid();
      await backendClient.createConversation(conversationId, agent.id, "webchat", `${agentName} deployments`);
      // Inject startup message as the first system-like user message
      const startupMsg = getAgentStartupMessage(agentName.replace(/^deploy-/, ""));
      await backendClient.saveUserMessage(conversationId, startupMsg);
    }

    // Save the notification as a user message and trigger a turn
    await backendClient.saveUserMessage(conversationId, message);
    console.log(`[deploy-notify] Sent deployment notification to ${agentName} (conversation=${conversationId})`);

    // Fire-and-forget: trigger the agent turn so it processes the message
    // The webchat channel will pick up the response if anyone is watching
    (async () => {
      try {
        for await (const _event of backendClient.conversationTurnStream(conversationId!, undefined, agent.id)) {
          // Consume the stream — responses are persisted by the backend
        }
      } catch (err: any) {
        console.warn(`[deploy-notify] Turn execution error for ${agentName}:`, err.message);
      }
    })();
  });

  // Auto-register GitHub webhooks (non-blocking — failures are logged, not fatal)
  // Discovers repos from SpacetimeDB agent workspace mounts unless explicit repos are configured.
  const registrar = new WebhookRegistrar({
    externalUrl: process.env.GATEWAY_EXTERNAL_URL,
    webhookSecret: process.env.GITHUB_WEBHOOK_SECRET,
    repos: config.webhooks?.repos,
    spacetimedb: {
      url: config.spacetimedbUrl,
      module: config.spacetimedbModuleName,
      token: config.spacetimedbToken,
    },
  });
  registrar.ensureWebhooks().catch((err) => {
    console.warn("[registrar] Unexpected error during webhook registration:", err);
  });

  const app = express();
  app.use(express.json({
    verify: (req: any, _res, buf) => {
      // Capture raw body for webhook signature verification
      req.rawBody = buf;
    },
  }));

  // CORS — allow frontend origin
  app.use((_req: any, res: any, next: any) => {
    res.header("Access-Control-Allow-Origin", "*");
    res.header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
    res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
    if (_req.method === "OPTIONS") return res.sendStatus(204);
    next();
  });

  // API key authentication middleware — skip /health and /webhooks
  app.use((req: any, res: any, next: any) => {
    if (req.path === "/health" || req.path.startsWith("/webhooks") || req.path.startsWith("/api/v1/workspace-files")) return next();
    const authHeader = req.headers.authorization;
    const token = authHeader?.startsWith("Bearer ") ? authHeader.slice(7) : null;
    if (token !== config.apiKey) {
      return res.status(401).json({ error: "Unauthorized — invalid or missing API key" });
    }
    next();
  });

  // SpacetimeDB token endpoint for frontend auth
  app.get("/api/v1/spacetimedb/token", (_req: any, res: any) => {
    // Serve the CLI token so the browser can authenticate as the same identity
    // Uses the same resolved token as the persistence router (env var OR cli.toml)
    const token = config.spacetimedbToken;
    if (!token) {
      return res.status(404).json({ error: "No SpacetimeDB token configured" });
    }
    res.json({ token });
  });

  // Persistence API for Agent Workers
  app.use("/api/v1", createPersistenceRouter(config));

  // Conversations API (backed by SpacetimeDB)
  app.use("/api/v1", createConversationsRouter(config));

  // Plans API (backed by SpacetimeDB)
  app.use("/api/v1", createPlansRouter(config));

  // GitHub webhook handler for repo update notifications

  // Branch manager (gateway owns all branch state)
  const branchManager = new BranchManager(config.backendUrl);

  const webhookRouter = createWebhookRouter({
    eventBus,
    onMainMerge: async () => {
      const pref = branchManager.getPreference("default");
      if (pref === "main") {
        const ok = await branchManager.notifyWorkerReload("main");
        console.log(`[webhook] main branch updated — worker reload ${ok ? "sent" : "skipped (offline)"}`);
      }
      webchat.broadcast({
        type: "branch_changed" as any,
        content: JSON.stringify({ branch: pref, reason: "main_merge" }),
      });
    },
    onPush: async (repo, branch, actor) => {
      webchat.broadcast({
        type: "webhook_push" as any,
        content: JSON.stringify({ repo, branch, actor }),
      });
      const pref = branchManager.getPreference("default");
      if (branch === pref) {
        const ok = await branchManager.notifyWorkerReload(pref);
        console.log(`[webhook] push to tracked branch '${pref}' — worker reload ${ok ? "sent" : "skipped (offline)"}`);
        webchat.broadcast({
          type: "branch_changed" as any,
          content: JSON.stringify({ branch: pref, reason: "push" }),
        });
      }
    },
  });
  app.use("/webhooks", webhookRouter);

  // Branch management API
  app.get("/api/v1/container/branches", async (_req: any, res: any) => {
    try {
      const branches = await branchManager.listBranches();
      res.json({ branches });
    } catch (err: any) {
      res.status(500).json({ error: err.message || "Failed to list branches" });
    }
  });

  // Helper: resolve worker URL for an agent (returns null to use default)
  async function resolveWorkerUrl(agentId?: string): Promise<{ workerUrl: string | null; containerId: string }> {
    if (!agentId) return { workerUrl: null, containerId: "default" };
    try {
      const resp = await fetch(`${config.backendUrl}/api/v1/agent/resolve?agent_id=${encodeURIComponent(agentId)}`, {
        headers: { "Authorization": `Bearer ${config.apiKey}` },
      });
      if (resp.ok) {
        const resolved = await resp.json();
        if (resolved.mode === "container" && resolved.worker_url) {
          return { workerUrl: resolved.worker_url, containerId: agentId };
        }
      }
    } catch (err) {
      console.warn("[branches] Failed to resolve agent worker:", (err as Error).message);
    }
    return { workerUrl: null, containerId: "default" };
  }

  app.get("/api/v1/container/branch", async (req: any, res: any) => {
    try {
      const agentId = req.query?.agent_id as string | undefined;
      const { workerUrl, containerId: resolvedId } = await resolveWorkerUrl(agentId);
      const containerId = agentId || resolvedId;
      const branch = branchManager.getPreference(containerId);
      const status = await branchManager.getWorkerStatus(workerUrl || undefined);
      res.json({
        container_id: containerId,
        branch,
        worker_online: status.online,
        worker_branch: status.branch,
        active_turns: status.activeTurns,
        pending_reload: status.pendingReload,
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message || "Failed to get branch" });
    }
  });

  app.post("/api/v1/container/branch", async (req: any, res: any) => {
    try {
      const { branch, agent_id: agentId } = req.body || {};
      if (!branch || typeof branch !== "string") {
        return res.status(400).json({ error: "branch is required" });
      }
      const { workerUrl, containerId: resolvedId } = await resolveWorkerUrl(agentId);
      // Use agent_id as the preference key even if resolve failed —
      // falling back to "default" would save the preference under the wrong key.
      const containerId = agentId || resolvedId;
      const result = await branchManager.setPreference(
        containerId,
        branch,
        workerUrl || undefined,
        config.backendUrl,
        agentId || undefined,
      );
      webchat.broadcast({
        type: "branch_changed" as any,
        content: JSON.stringify({ branch, reason: "user", deferred: result.deferred }),
      });
      res.json({
        ok: true,
        deferred: result.deferred,
        branch,
        ...(result.activeTurns !== null ? { active_turns: result.activeTurns } : {}),
      });
    } catch (err: any) {
      res.status(500).json({ error: err.message || "Failed to set branch" });
    }
  });

  // Event subscription API
  app.use("/api/v1/events", createEventsRouter(eventBus));

  // Session token support for Promotion API
  initSessionTokens(join(homedir(), ".bond", "data"));

  // Permission Broker (with gateway config for /deploy endpoint)
  app.use("/api/v1/broker", createBrokerRouter({
    dataDir: join(homedir(), ".bond", "data"),
    policyDir: join(homedir(), ".bond", "policies"),
  }, config));

  // Deployment API (environments, promotions, scripts, receipts)
  app.use("/api/v1/deployments", createDeploymentsRouter(config));

  // Backups API (list, preview, restore SpacetimeDB backups)
  app.use("/api/v1/backups", createBackupsRouter(config));

  // Channel management API and lifecycle
  const channelManager = new ChannelManager("data/channels.json", backendClient);

  // Build the message pipeline
  const allowListProvider: AllowListProvider = {
    getAllowList(channelType: string) {
      return channelManager.getAllowListForChannel(channelType);
    },
  };

  const pipeline = new MessagePipeline();
  pipeline.use(new RateLimitHandler());
  pipeline.use(new AuthHandler());
  pipeline.use(new AllowListHandler(allowListProvider));
  pipeline.use(new AgentResolver({
    getSelectedAgentId: () => null, // webchat/channels handle their own agent selection
    getConversationId: () => null,  // conversation IDs are pre-resolved by adapters
    generateConversationId: () => ulid(),
    setConversationId: () => {},    // adapters manage their own conversation tracking
  }));
  pipeline.use(new ContextLoader());
  pipeline.use(new TurnExecutor(backendClient));
  pipeline.use(new Persister());
  pipeline.use(new ResponseFanOut({
    getWatchers(conversationId: string) {
      const binding = channelManager.getChannelBinding(conversationId);
      const watchers: Array<{ channelType: string; channelId: string }> = [];
      if (binding) watchers.push(binding);
      // webchat sockets are handled separately via sendToConversation
      return watchers;
    },
    async sendToChannel(channelType: string, channelId: string, message: string) {
      await channelManager.pushToChannel(channelId, message);
    },
  }));

  // Wire pipeline into channels
  webchat.setPipeline(pipeline);
  channelManager.setPipeline(pipeline);

  app.use("/api/v1", createChannelRouter(channelManager));

  // SolidTime integration API
  app.use("/api/v1", createSolidTimeRouter());
  // Auto-start previously enabled channels (non-blocking)
  channelManager.autoStart().catch((err) => {
    console.warn("[gateway] Channel auto-start error:", err);
  });

  // Global Broadcast API for internal services
  app.post("/api/v1/broadcast", (req: any, res: any) => {
    webchat.broadcast(req.body);
    res.status(200).json({ status: "broadcasted" });
  });

  // Workspace file serving — serves generated images and other workspace files
  // (Design Doc 100, Phase 2: image rendering in chat UI)
  app.get("/api/v1/workspace-files/:path(*)", (req: any, res: any) => {
    const requestedPath = decodeURIComponent(req.params.path);

    // Security: resolve and validate the path is within allowed directories
    const { resolve, join } = require("node:path");
    const { existsSync, statSync, createReadStream } = require("node:fs");

    // Allow serving from workspace or .bond/images
    const allowedRoots = [
      resolve(process.env.WORKSPACE_DIR || "/workspace"),
      resolve(join(process.env.HOME || "/root", ".bond", "images")),
    ];

    let fullPath: string;
    if (requestedPath.startsWith("/")) {
      fullPath = resolve(requestedPath);
    } else {
      fullPath = resolve(join(process.env.WORKSPACE_DIR || "/workspace", requestedPath));
    }

    // Ensure the resolved path is under an allowed root (prevent path traversal)
    const isAllowed = allowedRoots.some((root: string) => fullPath.startsWith(root));
    if (!isAllowed) {
      return res.status(403).json({ error: "Access denied — path outside allowed directories" });
    }

    if (!existsSync(fullPath)) {
      return res.status(404).json({ error: "File not found" });
    }

    const stat = statSync(fullPath);
    if (!stat.isFile()) {
      return res.status(400).json({ error: "Not a file" });
    }

    // Determine content type from extension
    const ext = fullPath.split(".").pop()?.toLowerCase() || "";
    const mimeTypes: Record<string, string> = {
      png: "image/png",
      jpg: "image/jpeg",
      jpeg: "image/jpeg",
      gif: "image/gif",
      webp: "image/webp",
      svg: "image/svg+xml",
      bmp: "image/bmp",
      ico: "image/x-icon",
      pdf: "application/pdf",
      txt: "text/plain",
      json: "application/json",
      md: "text/markdown",
    };
    const contentType = mimeTypes[ext] || "application/octet-stream";

    res.setHeader("Content-Type", contentType);
    res.setHeader("Content-Length", stat.size);
    res.setHeader("Cache-Control", "public, max-age=3600");
    createReadStream(fullPath).pipe(res);
  });

  // HTTP server for health check + WS upgrade
  app.get("/health", (req: any, res: any) => {
    res.json({ status: "ok", service: "bond-gateway" });
  });

  const httpServer = createServer(app);

  const wss = new WebSocketServer({
    server: httpServer,
    path: "/ws",
  });

  wss.on("connection", (socket, req) => {
    // Authenticate WebSocket via ?token= query parameter
    const url = new URL(req.url || "", `http://${req.headers.host}`);
    const token = url.searchParams.get("token");
    if (token !== config.apiKey) {
      console.warn(`[gateway] WebSocket rejected — invalid token from ${req.socket.remoteAddress}`);
      socket.close(4001, "Unauthorized");
      return;
    }
    console.log(`[gateway] New WebSocket connection from ${req.socket.remoteAddress}`);
    webchat.handleConnection(socket);
  });

  httpServer.listen(config.port, config.host, () => {
    console.log(`[gateway] Bond gateway listening on ws://${config.host}:${config.port}/ws`);
    console.log(`[gateway] Backend URL: ${config.backendUrl}`);

    // Initialize the backup scheduler (replaces the old shell-script cron approach).
    // Handles tiered backups (hourly/daily/weekly/monthly), catch-up for missed windows,
    // and optional startup backup. Delayed 10s to let SpacetimeDB settle.
    setTimeout(() => {
      initBackupScheduler(config).catch(err => {
        console.warn("[gateway] Backup scheduler init failed (non-fatal):", err?.message ?? err);
      });
    }, 10_000);

    // Initialize SpacetimeDB real-time subscription for system events.
    // This enables the completion loop: when a background coding agent finishes,
    // the worker writes a system_event row → SpacetimeDB pushes it here via
    // WebSocket → CompletionHandler triggers an agent turn → user gets a summary.
    if (config.spacetimedbToken && config.spacetimedbUrl) {
      const completionHandler = new CompletionHandler(
        config,
        backendClient,
        (conversationId, message) => {
          webchat.sendToConversation(conversationId, message as any);
        },
      );

      initSubscription(config, (event) => {
        completionHandler.handleEvent(event).catch((err) => {
          console.error("[gateway] Completion handler error:", err);
        });
      })
        .then(() => {
          console.log("[gateway] SpacetimeDB subscription active — system events will trigger completion turns");
        })
        .catch((err) => {
          // Non-fatal: gateway works without subscriptions, just no auto-completions
          console.warn("[gateway] SpacetimeDB subscription failed (completion turns disabled):", err?.message ?? err);
        });
    } else {
      console.warn("[gateway] SpacetimeDB not configured — completion turns disabled");
    }
  });

  return {
    close() {
      eventBus.stopCleanup();
      wss.close();
      httpServer.close();
    },
  };
}
