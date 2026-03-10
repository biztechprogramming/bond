/**
 * AgentResolver — resolves which agent handles this message and which conversation it belongs to.
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";

export interface AgentResolverDeps {
  /** Get the selected agent ID for a channel session, or null for default */
  getSelectedAgentId(channelType: string, channelId: string): string | null;
  /** Get existing conversation ID for a channel+agent, or null */
  getConversationId(channelType: string, channelId: string, agentId: string | null): string | null;
  /** Generate a new conversation ID */
  generateConversationId(): string;
  /** Store the conversation ID for a channel+agent pair */
  setConversationId(channelType: string, channelId: string, agentId: string | null, conversationId: string): void;
}

export class AgentResolver implements PipelineHandler {
  name = "agent-resolver";

  constructor(private deps: AgentResolverDeps) {}

  async handle(message: PipelineMessage, context: PipelineContext, next: () => Promise<void>): Promise<void> {
    // Use agentId from message metadata if provided (webchat sends it), otherwise resolve from session
    const agentId = (message.metadata.agentId as string) || this.deps.getSelectedAgentId(message.channelType, message.channelId);
    message.agentId = agentId || undefined;

    // Use conversationId from message metadata if provided (webchat sends it)
    let conversationId = message.metadata.conversationId as string | undefined;

    if (!conversationId) {
      conversationId = this.deps.getConversationId(message.channelType, message.channelId, agentId) || undefined;
    }

    if (!conversationId) {
      conversationId = this.deps.generateConversationId();
      this.deps.setConversationId(message.channelType, message.channelId, agentId, conversationId);
    }

    message.conversationId = conversationId;
    await next();
  }
}
