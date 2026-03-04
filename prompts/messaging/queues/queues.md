# Message Queues

## When this applies
Working with point-to-point message queues (not streaming/pub-sub).

## Patterns / Gotchas
- Visibility timeout: message becomes invisible after dequeue — if consumer crashes before acknowledging, message reappears after timeout. Set timeout > max processing time
- FIFO vs standard: FIFO queues have lower throughput (300-3000 msg/s depending on service) — only use when ordering matters
- Message deduplication: most queues offer at-least-once, not exactly-once — use idempotency keys in your handler
- Large messages: most queues have size limits (256KB-1MB) — use claim-check pattern: store payload in blob storage, send reference in message
- Batch operations: dequeue in batches for throughput — but ack individually for reliability
- Retry with exponential backoff: 1s → 2s → 4s → 8s — cap at reasonable maximum (e.g., 5 minutes) to avoid infinite growth
- Queue depth monitoring: rising depth means consumers can't keep up — auto-scale consumers based on this metric
