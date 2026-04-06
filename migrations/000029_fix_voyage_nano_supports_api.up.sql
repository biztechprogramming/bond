-- Fix voyage-4-nano: it is a local-only model and does not support the Voyage REST API
UPDATE embedding_configs SET supports_api = 0 WHERE model_name = 'voyage-4-nano';
