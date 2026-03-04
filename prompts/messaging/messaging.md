# Messaging

## When this applies
Working with message queues, event streaming, or async communication patterns.

## Patterns / Gotchas
- At-least-once delivery is the norm — design consumers to be idempotent (processing same message twice produces same result)
- Message ordering: most queues guarantee ordering per partition/session, NOT globally — design for out-of-order processing
- Dead letter queues: always configure them — messages that fail N times need somewhere to go for inspection
- Poison messages: a malformed message that always fails can block an entire partition — implement max retry with DLQ redirect
- Backpressure: consumers must signal when overwhelmed — unbounded buffering causes OOM, not just slowness
