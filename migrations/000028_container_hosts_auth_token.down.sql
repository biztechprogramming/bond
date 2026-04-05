-- SQLite does not support DROP COLUMN before 3.35.0; recreate table without auth_token
CREATE TABLE container_hosts_backup AS SELECT
    id, name, host, port, user, ssh_key_encrypted, daemon_port, max_agents,
    memory_mb, labels, enabled, status, is_local, created_at, updated_at
FROM container_hosts;

DROP TABLE container_hosts;

CREATE TABLE container_hosts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    host TEXT NOT NULL,
    port INTEGER NOT NULL DEFAULT 22,
    user TEXT NOT NULL DEFAULT 'bond',
    ssh_key_encrypted TEXT,
    daemon_port INTEGER NOT NULL DEFAULT 8990,
    max_agents INTEGER NOT NULL DEFAULT 4,
    memory_mb INTEGER NOT NULL DEFAULT 0,
    labels TEXT NOT NULL DEFAULT '[]',
    enabled INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    is_local INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO container_hosts SELECT * FROM container_hosts_backup;
DROP TABLE container_hosts_backup;
