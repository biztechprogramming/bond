import asyncio
import json
import tempfile
from pathlib import Path
from backend.app.agent.tools.native import handle_file_read

async def test_parallel_file_read(tmp_path):
    """Parallel file_read using absolute paths (cwd-independent)."""
    p1 = tmp_path / "test1.txt"
    p2 = tmp_path / "test2.txt"
    p1.write_text("content 1")
    p2.write_text("content 2")

    args = {"paths": [str(p1), str(p2)]}
    res = await handle_file_read(args, {})

    assert len(res["results"]) == 2
    assert res["results"][0]["content"] == "content 1"
    assert res["results"][1]["content"] == "content 2"
