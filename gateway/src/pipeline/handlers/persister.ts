/**
 * Persister — pass-through handler that ensures the done event is processed.
 * The backend already handles actual persistence of messages.
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";

export class Persister implements PipelineHandler {
  name = "persister";

  async handle(message: PipelineMessage, _context: PipelineContext, next: () => Promise<void>): Promise<void> {
    // Backend already persists messages during the turn.
    // This handler is a placeholder for any gateway-side persistence needs.
    await next();
  }
}
