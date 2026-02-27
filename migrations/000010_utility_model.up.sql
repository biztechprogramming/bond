-- Migration 010: Add utility_model to agents for contextual fragment selection
ALTER TABLE agents ADD COLUMN utility_model TEXT NOT NULL DEFAULT 'claude-sonnet-4-6';
