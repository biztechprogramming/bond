-- Remove seeded embedding defaults (only if they still have the default values)
DELETE FROM settings WHERE key = 'embedding.model' AND value = 'voyage-4-nano';
DELETE FROM settings WHERE key = 'embedding.output_dimension' AND value = '1024';
DELETE FROM settings WHERE key = 'embedding.execution_mode' AND value = 'auto';
