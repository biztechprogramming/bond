INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('anthropic', 'Anthropic',  'anthropic', 'https://api.anthropic.com',                    '/v1/models?limit=100', 'anthropic_api',  'x-api-key',   '{"anthropic_version": "2023-06-01"}');

INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('google',    'Google',     'gemini',    'https://generativelanguage.googleapis.com',     '/v1beta/models',       'google_api',     'query_param', '{}');

INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('openai',    'OpenAI',     'openai',    'https://api.openai.com',                       '/v1/models',           'openai_compat',  'bearer',      '{}');

INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('deepseek',  'DeepSeek',   'deepseek',  'https://api.deepseek.com',                     '/models',              'openai_compat',  'bearer',      '{}');

INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('groq',      'Groq',       'groq',      'https://api.groq.com/openai',                  '/v1/models',           'openai_compat',  'bearer',      '{}');

INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('mistral',   'Mistral',    'mistral',   'https://api.mistral.ai',                       '/v1/models',           'openai_compat',  'bearer',      '{}');

INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('xai',       'xAI',        'xai',       'https://api.x.ai',                             '/v1/models',           'openai_compat',  'bearer',      '{}');

INSERT INTO providers (id, display_name, litellm_prefix, api_base_url, models_endpoint, models_fetch_method, auth_type, config) VALUES
    ('openrouter','OpenRouter',  'openrouter','https://openrouter.ai/api',                    '/v1/models',           'openai_compat',  'bearer',      '{}');

INSERT INTO provider_aliases (alias, provider_id) VALUES ('gemini',  'google');
INSERT INTO provider_aliases (alias, provider_id) VALUES ('claude',  'anthropic');
INSERT INTO provider_aliases (alias, provider_id) VALUES ('gpt',     'openai');
INSERT INTO provider_aliases (alias, provider_id) VALUES ('o1',      'openai');
INSERT INTO provider_aliases (alias, provider_id) VALUES ('o3',      'openai');
INSERT INTO provider_aliases (alias, provider_id) VALUES ('o4',      'openai');
