# Kafka

## When this applies
Working with Apache Kafka for event streaming.

## Patterns / Gotchas
- Consumer group rebalancing: adding/removing consumers triggers rebalance — ALL consumers in the group pause during rebalance (can take 30s+)
- `session.timeout.ms` vs `heartbeat.interval.ms`: heartbeat should be ~1/3 of session timeout — too-close values cause unnecessary rebalances
- Exactly-once semantics: requires `enable.idempotence=true` + `transactional.id` on producer AND `isolation.level=read_committed` on consumer — missing ANY piece breaks the guarantee
- `auto.offset.reset=latest` means NEW consumers miss all existing messages — use `earliest` for replay capability, `latest` only for real-time-only consumers
- Partition count is permanent (practically) — you can increase but NEVER decrease partitions without recreating the topic. Key-based routing breaks on partition count change
- `max.poll.interval.ms`: if processing takes longer than this, consumer is kicked from group — set high enough for your slowest message batch
- Consumer lag: partition count × avg lag = total unprocessed messages — monitor this, not just throughput
- Compacted topics: only latest value per key is retained — use for state/config, NOT for event logs
- `acks=all` (or `-1`): producer waits for ALL in-sync replicas — slower but durable. `acks=1` only waits for leader (can lose data on leader failure)
- Key serialization: same key always goes to same partition — changing key format breaks ordering guarantees for existing keys
- Schema Registry: use Avro/Protobuf schemas, not raw JSON — schema evolution with backward compatibility prevents consumer breakage
