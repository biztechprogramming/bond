/**
 * Bond Gateway entry point.
 */

import { loadConfig } from "./config.js";
import { startGatewayServer } from "./server.js";

const config = loadConfig();
const server = startGatewayServer(config);

console.log("[gateway] Bond gateway started");

// Graceful shutdown
process.on("SIGINT", () => {
  console.log("[gateway] Shutting down...");
  server.close();
  process.exit(0);
});

process.on("SIGTERM", () => {
  console.log("[gateway] Shutting down...");
  server.close();
  process.exit(0);
});
