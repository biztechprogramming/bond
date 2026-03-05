import { useEffect, useState } from 'react';
import { useSpacetimeDB, getMessagesForConversation } from '@/lib/spacetimedb-client';
import type { ConversationMessage } from '@/lib/spacetimedb-client';

/**
 * Hook to get messages for a specific conversation from SpacetimeDB.
 * Automatically updates when new messages are added to the conversation.
 */
export function useMessages(conversationId: string | null): ConversationMessage[] {
  return useSpacetimeDB(() => {
    if (!conversationId) return [];
    return getMessagesForConversation(conversationId);
  }, [conversationId]);
}

/**
 * Hook to get messages formatted for the ChatPanel component.
 * Converts ConversationMessage objects to ChatMessage objects.
 */
export function useChatMessages(conversationId: string | null) {
  const dbMessages = useMessages(conversationId);
  const [chatMessages, setChatMessages] = useState<any[]>([]);

  useEffect(() => {
    // Convert SpacetimeDB messages to chat messages format
    const formatted = dbMessages.map(msg => ({
      id: msg.id,
      role: msg.role as 'user' | 'assistant' | 'system',
      content: msg.content,
      // Add agentName for assistant messages if available
      ...(msg.role === 'assistant' ? { agentName: 'Agent' } : {})
    }));
    
    setChatMessages(formatted);
  }, [dbMessages]);

  return chatMessages;
}