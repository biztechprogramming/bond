/**
 * EventHistory — in-memory ring buffer for gateway events.
 * See design doc 040-gateway-event-subscriptions.md §3.2.4
 */

import type { GatewayEvent, EventFilter } from "./types.js";

function matchesFilter(filter: Partial<EventFilter>, event: GatewayEvent): boolean {
  if (filter.source && filter.source !== event.source) return false;
  if (filter.type && filter.type !== event.type) return false;
  if (filter.repo && filter.repo !== event.repo) return false;
  if (filter.branch && filter.branch !== event.branch) return false;
  if (filter.actor && filter.actor !== event.actor) return false;
  return true;
}

export class EventHistory {
  private events: GatewayEvent[] = [];
  private readonly maxAge: number = 24 * 60 * 60 * 1000; // 24 hours
  private readonly maxEvents: number = 10_000;
  private pruneInterval: ReturnType<typeof setInterval> | null = null;

  constructor() {
    // Prune every 10 minutes
    this.pruneInterval = setInterval(() => this.prune(), 10 * 60 * 1000);
    // Avoid keeping Node process alive just for this
    if (this.pruneInterval.unref) this.pruneInterval.unref();
  }

  append(event: GatewayEvent): void {
    this.events.push(event);
    // Enforce max capacity — drop oldest
    if (this.events.length > this.maxEvents) {
      this.events.splice(0, this.events.length - this.maxEvents);
    }
  }

  query(filter: Partial<EventFilter>, limit = 100): GatewayEvent[] {
    const results: GatewayEvent[] = [];
    // Iterate newest-first
    for (let i = this.events.length - 1; i >= 0 && results.length < limit; i--) {
      const event = this.events[i];
      if (matchesFilter(filter, event)) {
        results.push(event);
      }
    }
    return results;
  }

  prune(): void {
    const cutoff = Date.now() - this.maxAge;
    // Events are in chronological order — find first one newer than cutoff
    let start = 0;
    while (start < this.events.length && this.events[start].timestamp < cutoff) {
      start++;
    }
    if (start > 0) {
      this.events.splice(0, start);
    }
  }

  stop(): void {
    if (this.pruneInterval) {
      clearInterval(this.pruneInterval);
      this.pruneInterval = null;
    }
  }

  size(): number {
    return this.events.length;
  }
}
