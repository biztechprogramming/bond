/**
 * EventBus — in-memory pub/sub coordinator for gateway events.
 * See design doc 040-gateway-event-subscriptions.md §3.2.3
 */

import { ulid } from "ulid";
import type { GatewayEvent, EventFilter, EventSubscription } from "./types.js";
import { EventHistory } from "./event-history.js";

export function matches(filter: EventFilter, event: GatewayEvent): boolean {
  if (filter.source && filter.source !== event.source) return false;
  if (filter.type && filter.type !== event.type) return false;
  if (filter.repo && filter.repo !== event.repo) return false;
  if (filter.branch) {
    if (filter.branch.includes("*")) {
      // Glob match: "feature/*" matches "feature/fix-auth"
      const regex = new RegExp("^" + filter.branch.replace(/\*/g, ".*") + "$");
      if (!event.branch || !regex.test(event.branch)) return false;
    } else {
      if (filter.branch !== event.branch) return false;
    }
  }
  if (filter.actor && filter.actor !== event.actor) return false;
  return true;
}

export type EventHandler = (event: GatewayEvent, subscription: EventSubscription) => void;

export class EventBus {
  private subscriptions: Map<string, EventSubscription> = new Map();
  private history: EventHistory;
  private handlers: EventHandler[] = [];
  private cleanupInterval: ReturnType<typeof setInterval> | null = null;

  constructor(history?: EventHistory) {
    this.history = history ?? new EventHistory();
  }

  /**
   * Register a handler called whenever an event matches a subscription.
   * Used by CompletionDispatcher to receive matched events.
   */
  onMatch(handler: EventHandler): void {
    this.handlers.push(handler);
  }

  /**
   * Emit an event: persist to history, find matching subscriptions, dispatch.
   */
  emit(event: GatewayEvent): void {
    this.history.append(event);

    const matched = this.match(event);
    for (const sub of matched) {
      sub.deliveryCount++;
      // Dispatch to all registered handlers
      for (const handler of this.handlers) {
        try {
          handler(event, sub);
        } catch (err) {
          console.error("[event-bus] Handler error:", err);
        }
      }
      // Auto-unsubscribe when maxDeliveries reached
      if (sub.deliveryCount >= sub.maxDeliveries) {
        this.subscriptions.delete(sub.id);
      }
    }
  }

  /**
   * Register a subscription. Returns the subscription ID.
   */
  subscribe(
    sub: Omit<EventSubscription, "id" | "createdAt" | "deliveryCount">,
  ): string {
    const id = ulid();
    const subscription: EventSubscription = {
      ...sub,
      id,
      createdAt: Date.now(),
      deliveryCount: 0,
    };
    this.subscriptions.set(id, subscription);
    return id;
  }

  /**
   * Remove a subscription by ID. Returns true if it existed.
   */
  unsubscribe(id: string): boolean {
    return this.subscriptions.delete(id);
  }

  /**
   * Find all active (non-expired) subscriptions matching the event.
   */
  private match(event: GatewayEvent): EventSubscription[] {
    const now = Date.now();
    const result: EventSubscription[] = [];
    for (const sub of this.subscriptions.values()) {
      if (sub.expiresAt <= now) continue; // expired
      if (matches(sub.filter, event)) {
        result.push(sub);
      }
    }
    return result;
  }

  /**
   * Periodic cleanup of expired subscriptions.
   */
  cleanup(): void {
    const now = Date.now();
    for (const [id, sub] of this.subscriptions) {
      if (sub.expiresAt <= now) {
        this.subscriptions.delete(id);
      }
    }
  }

  /**
   * Start the periodic cleanup interval (every 60s).
   */
  startCleanup(): void {
    if (this.cleanupInterval) return;
    this.cleanupInterval = setInterval(() => this.cleanup(), 60_000);
    if (this.cleanupInterval.unref) this.cleanupInterval.unref();
  }

  /**
   * Stop the cleanup interval.
   */
  stopCleanup(): void {
    if (this.cleanupInterval) {
      clearInterval(this.cleanupInterval);
      this.cleanupInterval = null;
    }
    this.history.stop();
  }

  /**
   * List all active subscriptions.
   */
  getSubscriptions(): EventSubscription[] {
    return Array.from(this.subscriptions.values());
  }

  getHistory(): EventHistory {
    return this.history;
  }
}
