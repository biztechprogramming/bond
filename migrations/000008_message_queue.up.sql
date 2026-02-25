ALTER TABLE conversation_messages ADD COLUMN status TEXT NOT NULL DEFAULT 'delivered';
CREATE INDEX idx_cm_status ON conversation_messages(conversation_id, status)
  WHERE status = 'queued';
