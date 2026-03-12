/**
 * SpacetimeDB WebSocket subscription for real-time system events.
 *
 * Opens a persistent WebSocket connection to SpacetimeDB and subscribes
 * to the system_events table. When a new event is inserted (e.g., a
 * coding agent completion), the provided handler is called immediately.
 *
 * This complements the existing HTTP client (client.ts) — HTTP is used
 * for writes/queries, WebSocket for real-time push notifications.
 */

import { DbConnection } from "./index.js";
import type { GatewayConfig } from "../config/index.js";

export interface SystemEventRow {
  id: string;
  conversationId: string;
  agentId: string;
  eventType: string;
  summary: string;
  metadata: string;
  consumed: boolean;
  createdAt: bigint;
}

export type SystemEventHandler = (event: SystemEventRow) => void;

const RECONNECT_DELAY_MS = 5_000;
const MAX_RECONNECT_DELAY_MS = 60_000;

/**
 * Initialize a SpacetimeDB WebSocket subscription for system events.
 *
 * Returns the DbConnection on success. The connection will auto-reconnect
 * on disconnect with exponential backoff.
 */
export async function initSubscription(
  config: GatewayConfig,
  onEvent: SystemEventHandler,
): Promise<DbConnection> {
  // Convert HTTP URL to WebSocket URL
  const wsUri = config.spacetimedbUrl
    .replace(/^http:\/\//, "ws://")
    .replace(/^https:\/\//, "wss://");

  let reconnectDelay = RECONNECT_DELAY_MS;

  function connect(): Promise<DbConnection> {
    return new Promise((resolve, reject) => {
      let resolved = false;

      const conn = DbConnection.builder()
        .withUri(wsUri)
        .withDatabaseName(config.spacetimedbModuleName)
        .withToken(config.spacetimedbToken)
        .onConnect((ctx, identity) => {
          console.log("[stdb-sub] Connected, identity:", identity.toHexString());
          reconnectDelay = RECONNECT_DELAY_MS; // Reset on successful connect

          ctx.subscriptionBuilder()
            .onApplied(() => {
              console.log("[stdb-sub] Subscription active — watching system_events");

              // Process any unconsumed events that arrived before we subscribed
              // (e.g., events created while the gateway was down)
              try {
                for (const row of ctx.db.system_events.iter()) {
                  const event = row as unknown as SystemEventRow;
                  if (!event.consumed) {
                    onEvent(event);
                  }
                }
              } catch (err) {
                console.warn("[stdb-sub] Failed to drain existing events:", err);
              }

              if (!resolved) {
                resolved = true;
                resolve(conn);
              }
            })
            .onError((errCtx) => {
              console.error("[stdb-sub] Subscription error:", errCtx?.event);
            })
            .subscribe(["SELECT * FROM system_events"]);
        })
        .onConnectError((_ctx, err) => {
          console.error("[stdb-sub] Connection failed:", err);
          if (!resolved) {
            resolved = true;
            reject(err);
          }
          scheduleReconnect(config, onEvent);
        })
        .build();

      // Listen for new system events — this fires on every insert
      conn.db.system_events.onInsert((_ctx: unknown, row: unknown) => {
        const event = row as SystemEventRow;
        if (!event.consumed) {
          onEvent(event);
        }
      });
    });
  }

  return connect();
}

/**
 * Schedule a reconnection attempt with exponential backoff.
 */
function scheduleReconnect(
  config: GatewayConfig,
  onEvent: SystemEventHandler,
): void {
  let delay = RECONNECT_DELAY_MS;

  const attempt = () => {
    console.log(`[stdb-sub] Reconnecting in ${delay}ms...`);
    setTimeout(async () => {
      try {
        await initSubscription(config, onEvent);
        console.log("[stdb-sub] Reconnected successfully");
      } catch {
        delay = Math.min(delay * 2, MAX_RECONNECT_DELAY_MS);
        attempt();
      }
    }, delay);
  };

  attempt();
}
