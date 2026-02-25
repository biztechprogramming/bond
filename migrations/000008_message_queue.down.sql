DROP INDEX IF EXISTS idx_cm_status;
ALTER TABLE conversation_messages DROP COLUMN status;
