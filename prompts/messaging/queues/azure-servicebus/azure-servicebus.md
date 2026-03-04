# Azure Service Bus

## When this applies
Working with Azure Service Bus queues, topics, or subscriptions.

## Patterns / Gotchas
- Peek-lock vs receive-and-delete: peek-lock is default and safe (message remains until explicitly completed); receive-and-delete removes on delivery (faster but no retry)
- `complete()` vs `abandon()` vs `dead_letter()`: complete = success, abandon = retry later, dead_letter = permanent failure
- Sessions: enable for FIFO per session-id — but sessions pin to a single consumer; other consumers sit idle for that session
- Duplicate detection: enable on queue creation — uses `MessageId` within a time window; cannot be enabled after creation
- Scheduled messages: `schedule_message(msg, enqueue_time)` — message is invisible until scheduled time; useful for delayed processing
- `max_delivery_count`: default is 10 — after 10 failed deliveries, message goes to dead letter queue automatically
- Lock duration: default 60 seconds — if processing takes longer, call `renew_message_lock()` before expiry or message reappears
- Topics/subscriptions: message goes to ALL subscriptions (fan-out) — use SQL filter rules on subscription to filter; default subscription gets everything
- Prefetch: `prefetch_count` retrieves messages ahead of time — improves throughput but increases risk of lock expiry on prefetched messages
- Max message size: 256KB (Standard), 100MB (Premium) — use claim-check for larger payloads
- Connection string has `SharedAccessKey` — rotate keys regularly; use Managed Identity in production instead
