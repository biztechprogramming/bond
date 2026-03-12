/**
 * Core types for the gateway event subscription system.
 * See design doc 040-gateway-event-subscriptions.md §3.2.1 and §3.2.2
 */

export interface GatewayEvent {
  id: string;                    // ulid
  source: string;                // "github", "ci", "internal"
  type: string;                  // "push", "pull_request", "check_run"
  repo: string;                  // "owner/repo"
  branch?: string;               // "feature/fix-auth"
  actor?: string;                // "github-username"
  payload: Record<string, any>;  // raw webhook payload (trimmed)
  timestamp: number;             // unix ms
}

export interface EventFilter {
  source?: string;               // "github"
  type?: string;                 // "push"
  repo?: string;                 // "owner/repo"
  branch?: string;               // exact or glob: "feature/*"
  actor?: string;                // "github-username"
}

export interface EventSubscription {
  id: string;                    // ulid
  conversationId: string;        // which conversation to notify
  agentId: string;               // which agent is subscribed
  filter: EventFilter;           // what to match on
  context: string;               // human-readable context for the LLM
  createdAt: number;             // unix ms
  expiresAt: number;             // unix ms — auto-cleanup
  maxDeliveries: number;         // default 1 — auto-unsubscribe after N deliveries
  deliveryCount: number;         // how many times matched so far
}
