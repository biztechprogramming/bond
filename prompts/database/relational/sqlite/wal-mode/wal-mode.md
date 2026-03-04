# WAL Mode

## When this applies
Configuring or debugging SQLite WAL (Write-Ahead Logging) mode.

## Patterns / Gotchas
- WAL creates two additional files: `.db-wal` and `.db-shm` — both MUST be on the same filesystem as the main db
- WAL file grows unbounded without checkpointing — `PRAGMA wal_autocheckpoint=1000` (default) triggers checkpoint every 1000 pages
- Manual checkpoint: `PRAGMA wal_checkpoint(TRUNCATE)` — TRUNCATE mode reclaims disk space, PASSIVE does not
- Readers can proceed during checkpointing, but a long-running read transaction blocks WAL truncation
- WAL mode is incompatible with network filesystems (NFS, SMB) — undefined behavior, silent corruption
- If `.db-shm` is deleted while connections are active, database corruption is likely
- WAL mode survives database close/reopen — it's set on the file, not the connection
- `PRAGMA journal_mode=DELETE` reverts to traditional rollback journal (requires exclusive lock)
- Bond sets `synchronous=NORMAL` with WAL — this means a power loss can lose the last transaction but won't corrupt the DB
- Multiple processes can read concurrently, but only one process should write (use busy_timeout to queue)
- WAL file not visible to backup tools that copy only `.db` — always copy all three files together
