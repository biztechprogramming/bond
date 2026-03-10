/**
 * Bond Gateway — WebSocket server.
 *
 * Handles WebSocket connections from the frontend webchat,
 * routes messages to the Python backend, and streams responses back.
 */

import { WebSocketServer } from "ws";
import { createServer } from "http";
import express from "express";
import type { GatewayConfig } from "./config/index.js";
import { SessionManager } from "./sessions/index.js";
import { BackendClient } from "./backend/index.js";
import { WebChatChannel } from "./channels/index.js";
import { createPersistenceRouter } from "./persistence/index.js";
import { createConversationsRouter } from "./conversations/index.js";
import { createPlansRouter } from "./plans/index.js";
import { createWebhookRouter } from "./webhooks.js";
import { ChannelManager } from "./channels/manager.js";
import { createChannelRouter } from "./channels/routes.js";

export interface GatewayServer {
  close(): void;
}

export function startGatewayServer(config: GatewayConfig): GatewayServer {
  const sessionManager = new SessionManager();
  const backendClient = new BackendClient(config.backendUrl);
  const webchat = new WebChatChannel(sessionManager, backendClient);

  const app = express();
  app.use(express.json());

  // CORS — allow frontend origin
  app.use((_req: any, res: any, next: any) => {
    res.header("Access-Control-Allow-Origin", "*");
    res.header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
    res.header("Access-Control-Allow-Headers", "Content-Type, Authorization");
    if (_req.method === "OPTIONS") return res.sendStatus(204);
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
  // Raw body capture middleware for signature verification
  app.use("/webhooks/github", (req: any, _res: any, next: any) => {
    let data: Buffer[] = [];
    req.on("data", (chunk: Buffer) => data.push(chunk));
    req.on("end", () => {
      (req as any).rawBody = Buffer.concat(data);
      // Parse JSON body manually since express.json() may have already consumed it
      try {
        req.body = JSON.parse((req as any).rawBody.toString());
      } catch {
        // body will be parsed by express.json() fallback
      }
      next();
    });
  });

  const webhookRouter = createWebhookRouter({
    onMainMerge: async () => {
      // Notify all known workers to reload
      console.log("[webhook] TODO: notify connected workers to /reload");
    },
  });
  app.use("/webhooks", webhookRouter);

  // Channel management API and lifecycle
  const channelManager = new ChannelManager("data/channels.json", backendClient);
  webchat.setChannelManager(channelManager);
  app.use("/api/v1", createChannelRouter(channelManager));
  // Auto-start previously enabled channels (non-blocking)
  channelManager.autoStart().catch((err) => {
    console.warn("[gateway] Channel auto-start error:", err);
  });

  // Global Broadcast API for internal services
  app.post("/api/v1/broadcast", (req: any, res: any) => {
    webchat.broadcast(req.body);
    res.status(200).json({ status: "broadcasted" });
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
    console.log(`[gateway] New WebSocket connection from ${req.socket.remoteAddress}`);
    webchat.handleConnection(socket);
  });

  httpServer.listen(config.port, config.host, () => {
    console.log(`[gateway] Bond gateway listening on ws://${config.host}:${config.port}/ws`);
    console.log(`[gateway] Backend URL: ${config.backendUrl}`);
  });

  return {
    close() {
      wss.close();
      httpServer.close();
    },
  };
}
