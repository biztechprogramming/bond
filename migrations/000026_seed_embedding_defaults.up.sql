-- Seed default embedding settings so the engine never starts unconfigured
INSERT INTO settings (key, value) VALUES
    ('embedding.model', 'voyage-4-nano'),
    ('embedding.output_dimension', '1024'),
    ('embedding.execution_mode', 'auto')
ON CONFLICT(key) DO NOTHING;
