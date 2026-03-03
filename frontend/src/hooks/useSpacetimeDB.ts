/**
 * React hooks for SpacetimeDB subscriptions.
 */

"use client";

import { useEffect, useState } from "react";
import {
  connectToSpacetimeDB,
  onDataChange,
  getConversations,
  getMessagesForConversation,
  getConversation,
  getAgentName,
  type Conversation,
  type ConversationMessage,
} from "@/lib/spacetimedb-client";

export function useSpacetimeConnection() {
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    connectToSpacetimeDB()
      .then(() => setConnected(true))
      .catch((err) => setError(err?.message || "Failed to connect"));
  }, []);

  return { connected, error };
}

function useSpacetimeData<T>(selector: () => T): T {
  const [data, setData] = useState<T>(selector);

  useEffect(() => {
    setData(selector());
    const unsub = onDataChange(() => setData(selector()));
    return unsub;
  }, []);

  return data;
}

export function useConversations(): Conversation[] {
  return useSpacetimeData(getConversations);
}

export function useConversation(id: string | null): Conversation | null {
  return useSpacetimeData(() => (id ? getConversation(id) : null));
}

export function useConversationMessages(conversationId: string | null): ConversationMessage[] {
  return useSpacetimeData(() =>
    conversationId ? getMessagesForConversation(conversationId) : []
  );
}

export function useAgentName(agentId: string | null): string | null {
  return useSpacetimeData(() => (agentId ? getAgentName(agentId) : null));
}
