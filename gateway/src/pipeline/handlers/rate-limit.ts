/**
 * RateLimitHandler — per-user sliding window rate limiting.
 */

import type { PipelineMessage, PipelineContext, PipelineHandler } from "../types.js";

interface RateLimitConfig {
  /** Max messages per minute (default 20) */
  perMinute?: number;
  /** Max messages per hour (default 200) */
  perHour?: number;
}

export class RateLimitHandler implements PipelineHandler {
  name = "rate-limit";

  private minuteWindows = new Map<string, number[]>();
  private hourWindows = new Map<string, number[]>();
  private perMinute: number;
  private perHour: number;

  constructor(config: RateLimitConfig = {}) {
    this.perMinute = config.perMinute ?? 20;
    this.perHour = config.perHour ?? 200;
  }

  async handle(message: PipelineMessage, context: PipelineContext, next: () => Promise<void>): Promise<void> {
    const key = message.userId || `${message.channelType}:${message.channelId}`;
    const now = Date.now();

    if (this.isRateLimited(key, now)) {
      await context.respond("Too many messages. Try again in a moment.");
      return;
    }

    this.record(key, now);
    await next();
  }

  private isRateLimited(key: string, now: number): boolean {
    const minuteWindow = this.getWindow(this.minuteWindows, key, now, 60_000);
    if (minuteWindow.length >= this.perMinute) return true;

    const hourWindow = this.getWindow(this.hourWindows, key, now, 3_600_000);
    if (hourWindow.length >= this.perHour) return true;

    return false;
  }

  private record(key: string, now: number): void {
    this.pushToWindow(this.minuteWindows, key, now);
    this.pushToWindow(this.hourWindows, key, now);
  }

  private getWindow(windows: Map<string, number[]>, key: string, now: number, windowMs: number): number[] {
    const timestamps = windows.get(key) || [];
    const filtered = timestamps.filter((t) => now - t < windowMs);
    windows.set(key, filtered);
    return filtered;
  }

  private pushToWindow(windows: Map<string, number[]>, key: string, now: number): void {
    const timestamps = windows.get(key) || [];
    timestamps.push(now);
    windows.set(key, timestamps);
  }
}
