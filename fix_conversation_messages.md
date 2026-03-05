## Fix: Agent Responses Not Appearing in Conversations

**Problem:** Agent responses were being saved to `messages` table (for logging/debugging) but not to `conversationMessages` table (for conversation history). The frontend reads from `conversationMessages` table, so agent responses weren't visible.

**Solution:** Created separate persistence methods for conversation messages vs. log messages.

### Changes Made:

1. **Gateway (`gateway/src/persistence/router.ts`)**:
   - Added new endpoint `POST /conversation-messages`
   - Calls `add_conversation_message` reducer to save to `conversationMessages` table
   - Updates conversation `messageCount` automatically

2. **Persistence Client (`backend/app/agent/persistence_client.py`)**:
   - Added `save_conversation_message()` method
   - In `api` mode: calls Gateway `/conversation-messages` endpoint
   - In `sqlite` mode: inserts into local `conversation_messages` table

3. **Worker (`backend/app/worker.py`)**:
   - Changed from `save_message()` to `save_conversation_message()` for:
     - User messages (at start of agent turn)
     - Assistant responses (after LLM completion)
   - Conversation messages now go to correct table

### Architecture:
- **`conversationMessages` table**: Conversation history (what users see)
- **`messages` table**: Logging/debugging messages (errors, coding messages)
- **`tool_logs` table**: Tool execution logs

### Verification:
- Gateway endpoint works: `POST /conversation-messages` saves to `conversationMessages` table
- Messages have correct `conversation_id`, `role`, `content`, `status="delivered"`
- Frontend `GET /conversations/{id}/messages` will now see agent responses
- Existing `save_message()` still available for logging/debugging if needed

### Status: ✅ Fixed
Agent responses now persist to `conversationMessages` table and will appear in conversations.