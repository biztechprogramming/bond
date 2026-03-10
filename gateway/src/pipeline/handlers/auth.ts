/**
 * AuthHandler — maps channel IDs to user identities.
 * For now, all authenticated users map to "owner".
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";

export class AuthHandler implements PipelineHandler {
  name = "auth";

  async handle(message: PipelineMessage, context: PipelineContext, next: () => Promise<void>): Promise<void> {
    // For now all channel users are the owner
    message.userId = "owner";
    await next();
  }
}
