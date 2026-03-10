/**
 * ResponseFanOut — pushes the complete response to all channels watching this conversation,
 * except the originating channel (which already received the stream).
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";

export interface FanOutTarget {
  channelType: string;
  channelId: string;
}

export interface FanOutDeps {
  /** Get all channels watching a conversation */
  getWatchers(conversationId: string): FanOutTarget[];
  /** Send a message to a specific channel */
  sendToChannel(channelType: string, channelId: string, message: string, senderLabel?: string): Promise<void>;
}

export class ResponseFanOut implements PipelineHandler {
  name = "response-fan-out";

  constructor(private deps: FanOutDeps) {}

  async handle(message: PipelineMessage, context: PipelineContext, next: () => Promise<void>): Promise<void> {
    if (message.conversationId && message.response) {
      const watchers = this.deps.getWatchers(message.conversationId);
      for (const watcher of watchers) {
        // Skip the originating channel type — it already received the response
        // via its own streaming/accumulation mechanism.
        // We compare by channelType (not channelId) because adapters use
        // different ID schemes (webchat uses sessionId, telegram uses chatId).
        if (watcher.channelType === message.channelType) {
          continue;
        }
        await this.deps.sendToChannel(watcher.channelType, watcher.channelId, message.response).catch(() => {});
      }
    }

    await next();
  }
}
