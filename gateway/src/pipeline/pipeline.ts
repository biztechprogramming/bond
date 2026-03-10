/**
 * MessagePipeline — ordered middleware chain for processing messages.
 */

import type { PipelineMessage, PipelineHandler, PipelineContext } from "./types.js";

export class MessagePipeline {
  private handlers: PipelineHandler[] = [];

  use(handler: PipelineHandler): this {
    this.handlers.push(handler);
    return this;
  }

  async execute(message: PipelineMessage, context: PipelineContext): Promise<void> {
    let index = 0;

    const next = async (): Promise<void> => {
      if (context.aborted) return;
      if (index >= this.handlers.length) return;

      const handler = this.handlers[index++];
      await handler.handle(message, context, next);
    };

    await next();
  }
}
