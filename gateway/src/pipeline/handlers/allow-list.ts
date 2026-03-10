/**
 * AllowListHandler — enforces per-channel allow-lists.
 * WebChat is always allowed; Telegram/WhatsApp check the allow-list.
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";
import type { AllowList } from "../../channels/allowlist.js";

export interface AllowListProvider {
  /** Get the AllowList for a channel type, or null if no restriction */
  getAllowList(channelType: string): AllowList | null;
}

export class AllowListHandler implements PipelineHandler {
  name = "allow-list";

  constructor(private provider: AllowListProvider) {}

  async handle(message: PipelineMessage, context: PipelineContext, next: () => Promise<void>): Promise<void> {
    // WebChat is always allowed (authenticated via session)
    if (message.channelType === "webchat") {
      await next();
      return;
    }

    const allowList = this.provider.getAllowList(message.channelType);
    if (allowList && !allowList.isAllowed(message.channelId)) {
      // Silent reject — don't reveal the bot exists
      return;
    }

    await next();
  }
}
