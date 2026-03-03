/**
 * SpacetimeDB client for the Bond frontend.
 *
 * Provides a singleton connection and React-friendly subscription hooks.
 * The frontend subscribes directly to SpacetimeDB tables — when a reducer
 * inserts/updates/deletes a row, the subscription pushes the change
 * to the browser instantly.
 */

import { DbConnection, tables } from "./spacetimedb/index";

// Types inferred from generated bindings
export interface Conversation {
  id: string;
  agentId: string;
  channel: string;
  title: string;
  isActive: boolean;
  messageCount: number;
  rollingSummary: string;
  summaryCoversto: number;
  recentToolsUsed: string;
  createdAt: bigint;
  updatedAt: bigint;
}

export interface ConversationMessage {
  id: string;
  conversationId: string;
  role: string;
  content: string;
  toolCalls: string;
  toolCallId: string;
  tokenCount: number;
  status: string;
  createdAt: bigint;
}

// ── Singleton connection ──

let db: DbConnection | null = null;
let connectionPromise: Promise<DbConnection> | null = null;

type Listener = () => void;
const listeners: Set<Listener> = new Set();

export function onDataChange(fn: Listener): () => void {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function notifyListeners() {
  listeners.forEach((fn) => fn());
}

export function getConnection(): DbConnection | null {
  return db;
}

export async function connectToSpacetimeDB(
  uri = "ws://localhost:18788/stdb/",
  moduleName = "bond-core"
): Promise<DbConnection> {
  if (db) return db;
  if (connectionPromise) return connectionPromise;

  connectionPromise = new Promise<DbConnection>(async (resolve, reject) => {
    try {
      // Fetch CLI token from same-origin proxy endpoint
      let token: string | null = null;
      try {
        const res = await fetch("/api/stdb-ws-token", { method: "POST" });
        if (res.ok) {
          const data = await res.json();
          token = data.token || null;
        }
      } catch {}

      let builder = DbConnection.builder()
        .withUri(uri)
        .withDatabaseName(moduleName);

      if (token) {
        builder = builder.withToken(token);
      }

      const conn = builder
        .onConnect((ctx, identity, _authToken) => {
          console.log("[spacetimedb] Connected, identity:", identity.toHexString());
          db = conn;

          ctx.subscriptionBuilder()
            .onApplied(() => {
              const count = [...ctx.db.conversations.iter()].length;
              console.log(`[spacetimedb] Subscribed — ${count} conversations`);
              notifyListeners();
              resolve(conn);
            })
            .onError((errCtx) => {
              console.error("[spacetimedb] Subscription error:", errCtx?.event);
            })
            .subscribe(["SELECT * FROM conversations", "SELECT * FROM conversation_messages"]);
        })
        .onConnectError((ctx, err) => {
          console.error("[spacetimedb] Connection failed:", err);
          connectionPromise = null;
          reject(err);
        })
        .build();

      // Table change listeners
      conn.db.conversations.onInsert(() => notifyListeners());
      conn.db.conversations.onUpdate(() => notifyListeners());
      conn.db.conversations.onDelete(() => notifyListeners());
      conn.db.conversationMessages.onInsert(() => notifyListeners());
      conn.db.conversationMessages.onDelete(() => notifyListeners());
    } catch (err) {
      connectionPromise = null;
      reject(err);
    }
  });

  return connectionPromise;
}

// ── Data accessors ──

export function getConversations(): Conversation[] {
  if (!db) return [];
  const rows: Conversation[] = [];
  for (const row of db.db.conversations.iter()) {
    rows.push(row as unknown as Conversation);
  }
  rows.sort((a, b) => (b.updatedAt > a.updatedAt ? 1 : b.updatedAt < a.updatedAt ? -1 : 0));
  return rows;
}

export function getConversation(id: string): Conversation | null {
  if (!db) return null;
  const row = db.db.conversations.id.find(id);
  return row ? (row as unknown as Conversation) : null;
}

export function getMessagesForConversation(conversationId: string): ConversationMessage[] {
  if (!db) return [];
  const msgs: ConversationMessage[] = [];
  for (const row of db.db.conversationMessages.iter()) {
    const msg = row as unknown as ConversationMessage;
    if (msg.conversationId === conversationId) {
      msgs.push(msg);
    }
  }
  msgs.sort((a, b) => (a.createdAt > b.createdAt ? 1 : a.createdAt < b.createdAt ? -1 : 0));
  return msgs;
}

export function getAgentName(agentId: string): string | null {
  if (!db) return null;
  const agent = db.db.agents.id.find(agentId);
  return agent ? (agent as any).displayName : null;
}
