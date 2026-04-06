-- Revert: restore supports_api flag for voyage-4-nano
UPDATE embedding_configs SET supports_api = 1 WHERE model_name = 'voyage-4-nano';
