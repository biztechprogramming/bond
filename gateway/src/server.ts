/**
 * Bond Gateway — WebSocket server.
 *
 * Handles WebSocket connections from the frontend webchat,
 * routes messages to the Python backend, and streams responses back.
 */

import { WebSocketServer } from "ws";
import { createServer } from "http";
import type { GatewayConfig } from "./config.js";
import { SessionManager } from "./sessions/manager.js";
import { BackendClient } from "./backend/client.js";
import { WebChatChannel } from "./channels/webchat.js";

export interface GatewayServer {
  close(): void;
}

export function startGatewayServer(config: GatewayConfig): GatewayServer {
  const sessionManager = new SessionManager();
  const backendClient = new BackendClient(config.backendUrl);
  const webchat = new WebChatChannel(sessionManager, backendClient);

  // HTTP server for health check + WS upgrade
  const httpServer = createServer((req, res) => {
    if (req.url === "/health") {
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok", service: "bond-gateway" }));
      return;
    }
    res.writeHead(404);
    res.end();
  });

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
