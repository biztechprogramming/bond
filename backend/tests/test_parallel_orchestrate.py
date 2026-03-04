import asyncio
import json
from backend.app.agent.tools.native import handle_parallel_orchestrate, handle_file_read
from backend.app.agent.tools.native_registry import build_native_registry
from pathlib import Path

async def test_parallel_orchestrate():
    # Setup dummy files
    p1 = Path("./p1.txt")
    p1.write_text("parallel content")
    
    # Define a plan with one batch containing two calls
    plan = {
        "batches": [
            {
                "batch_name": "Batch 1",
                "calls": [
                    {
                        "tool_name": "file_read",
                        "arguments": {"path": "./p1.txt"},
                        "description": "Read p1"
                    },
                    {
                        "tool_name": "file_read",
                        "arguments": {"path": "./p1.txt"},
                        "description": "Read p1 again"
                    }
                ]
            }
        ]
    }
    
    args = {"plan": plan}
    res = await handle_parallel_orchestrate(args, {})
    
    print(f"Parallel Orchestrate results: {json.dumps(res, indent=2)}")
    assert res["status"] == "completed"
    assert len(res["results"]) == 1
    assert len(res["results"][0]["results"]) == 2
    assert res["results"][0]["results"][0]["content"] == "parallel content"
    
    p1.unlink()
    print("Test passed!")

if __name__ == "__main__":
    asyncio.run(test_parallel_orchestrate())
