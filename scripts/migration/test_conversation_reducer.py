import asyncio
from backend.app.core.spacetimedb import StdbClient

async def test_reducer():
    stdb = StdbClient()
    
    # Test create_conversation reducer (we know this works from the code)
    conv_id = "test-conv-123"
    success = await stdb.call_reducer("create_conversation", [
        conv_id,
        "01JBOND0000000000000DEFAULT",
        "webchat",
        "Test Conversation",
    ])
    print(f"create_conversation: {success}")
    
    # Test add_conversation_message reducer
    if success:
        msg_id = "test-msg-123"
        success2 = await stdb.call_reducer("add_conversation_message", [
            msg_id,
            conv_id,
            "user",
            "Hello world",
            "",
            "",
            0,
            "delivered",
        ])
        print(f"add_conversation_message: {success2}")
    
    await stdb.close()

if __name__ == "__main__":
    asyncio.run(test_reducer())