/**
 * ContextLoader — lightweight gateway-side context resolution.
 * The backend handles API keys, history, system prompt loading.
 * This handler resolves any agent metadata the gateway needs.
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";

export class ContextLoader implements PipelineHandler {
  name = "context-loader";

  async handle(message: PipelineMessage, context: PipelineContext, next: () => Promise<void>): Promise<void> {
    // Gateway-side context is lightweight — the backend loads API keys, history, etc.
    // This handler is a placeholder for future gateway-side context needs.
    await next();
  }
}
