/**
 * Base channel adapter interface.
 *
 * Each channel (webchat, telegram, discord, etc.) implements this interface.
 */

export interface ChannelMessage {
  channelType: string;
  senderId: string;
  content: string;
  sessionId?: string;
  metadata?: Record<string, unknown>;
}

export interface ChannelAdapter {
  readonly channelType: string;
  start(): Promise<void>;
  stop(): Promise<void>;
}
