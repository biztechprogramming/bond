/**
 * Pipeline types — PipelineMessage, PipelineHandler, PipelineContext.
 */

export interface PipelineMessage {
  /** ULID */
  id: string;
  /** "webchat" | "telegram" | "whatsapp" | ... */
  channelType: string;
  /** Channel-specific sender ID (session ID for webchat, chat ID for telegram, etc.) */
  channelId: string;

  /** The user's message text */
  content: string;

  // Resolved by handlers as message flows through pipeline
  userId?: string;
  agentId?: string;
  conversationId?: string;

  /** Full agent response text (accumulated by TurnExecutor) */
  response?: string;
  /** Agent name resolved during turn */
  agentName?: string;

  timestamp: number;
  metadata: Record<string, any>;
}

export interface PipelineContext {
  /** Send a response back to the originating channel */
  respond(text: string): Promise<void>;

  /** Send a response to ALL channels watching this conversation */
  broadcast(text: string): Promise<void>;

  /** Stream a chunk to all watching channels */
  streamChunk(chunk: string): Promise<void>;

  /** Abort the pipeline with an error */
  abort(reason: string): Promise<void>;

  /** Emit an SSE event to watching channels (status, tool_call, plan events, etc.) */
  emit(event: string, data: Record<string, any>): Promise<void>;

  /** Whether the pipeline has been aborted */
  aborted: boolean;
}

export interface PipelineHandler {
  name: string;
  handle(
    message: PipelineMessage,
    context: PipelineContext,
    next: () => Promise<void>,
  ): Promise<void>;
}
