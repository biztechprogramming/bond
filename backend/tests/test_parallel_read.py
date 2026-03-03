import asyncio
import os
import json
from pathlib import Path
from backend.app.agent.tools.native import handle_file_read

async def test_parallel_file_read():
    # Setup dummy files in current directory
    p1 = Path("./test1.txt")
    p2 = Path("./test2.txt")
    p1.write_text("content 1")
    p2.write_text("content 2")
    
    # Execute parallel read
    args = {"paths": ["./test1.txt", "./test2.txt"]}
    res = await handle_file_read(args, {})
    
    print(f"Parallel read results: {json.dumps(res, indent=2)}")
    assert len(res["results"]) == 2
    assert res["results"][0]["content"] == "content 1"
    assert res["results"][1]["content"] == "content 2"
    
    p1.unlink()
    p2.unlink()
    print("Test passed!")

if __name__ == "__main__":
    asyncio.run(test_parallel_file_read())
