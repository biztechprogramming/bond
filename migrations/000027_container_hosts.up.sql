CREATE TABLE IF NOT EXISTS container_hosts (
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

-- Seed local host
INSERT INTO container_hosts (id, name, host, port, user, max_agents, memory_mb, is_local, status)
VALUES ('local', 'Local Machine', 'localhost', 0, '', 4, 0, 1, 'active')
ON CONFLICT(id) DO NOTHING;

-- Seed container default settings
INSERT INTO settings (key, value) VALUES
    ('container.default_image', 'bond-worker:latest'),
    ('container.memory_limit_mb', '2048'),
    ('container.cpu_limit', '2.0'),
    ('container.placement_strategy', 'least-loaded'),
    ('container.startup_command', ''),
    ('container.extra_packages', ''),
    ('container.workspace_mount_path', '/workspace'),
    ('container.ssh_key_path', '~/.ssh/id_rsa'),
    ('container.auto_pull_image', 'true'),
    ('container.max_local_agents', '4')
ON CONFLICT(key) DO NOTHING;
