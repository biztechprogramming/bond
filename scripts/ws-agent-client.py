#!/usr/bin/env python3
"""WebSocket client for Bond gateway — sends a task and collects the response.

Connects to the gateway WebSocket, waits for the "connected" event,
sends a chat message, and collects all events until "done".

Writes:
  --response-file: JSON with {response, conversation_id, tool_calls, duration_s}
  --events-file:   JSONL of every WS event received
"""

import argparse
import asyncio
import json
import sys
import time


async def run(args: argparse.Namespace) -> int:
    import websockets

    response_chunks: list[str] = []
    conversation_id = "unknown"
    tool_calls: list[dict] = []
    session_id: str | None = None
    done = False

    events_fh = open(args.events_file, "w") if args.events_file else None

    try:
        async with websockets.connect(args.ws_url) as ws:
            # Wait for "connected" event
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            if events_fh:
                events_fh.write(json.dumps(msg) + "\n")

            if msg.get("type") != "connected":
                print(f"Expected 'connected', got: {msg}", file=sys.stderr)
                return 1

            session_id = msg.get("sessionId")
            print(f"Connected to gateway, session={session_id}", file=sys.stderr)

            # Send the task message
            outgoing: dict = {
                "type": "message",
                "content": args.message,
            }
            if args.agent_id:
                outgoing["agentId"] = args.agent_id

            await ws.send(json.dumps(outgoing))
            print("Task sent, waiting for agent response...", file=sys.stderr)

            # Collect events until "done"
            start = time.monotonic()
            while not done:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
                except asyncio.TimeoutError:
                    print(f"Timeout after {args.timeout}s waiting for agent", file=sys.stderr)
                    return 2

                msg = json.loads(raw)
                if events_fh:
                    events_fh.write(json.dumps(msg) + "\n")

                evt_type = msg.get("type", "")

                if evt_type == "chunk":
                    content = msg.get("content", "")
                    if content:
                        response_chunks.append(content)

                elif evt_type == "tool_call":
                    tool_calls.append(msg)
                    tool_name = msg.get("name", msg.get("tool", "?"))
                    print(f"  tool_call: {tool_name}", file=sys.stderr)

                elif evt_type == "status":
                    status = msg.get("agentStatus", "")
                    conv = msg.get("conversationId")
                    if conv:
                        conversation_id = conv
                    print(f"  status: {status}", file=sys.stderr)

                elif evt_type == "done":
                    conv = msg.get("conversationId")
                    if conv:
                        conversation_id = conv
                    done = True

                elif evt_type == "error":
                    err = msg.get("error", "unknown error")
                    print(f"ERROR from agent: {err}", file=sys.stderr)
                    return 3

            elapsed = time.monotonic() - start

    except Exception as e:
        print(f"WebSocket error: {e}", file=sys.stderr)
        return 4
    finally:
        if events_fh:
            events_fh.close()

    # Write response file
    full_response = "".join(response_chunks)
    result = {
        "response": full_response,
        "conversation_id": conversation_id,
        "tool_calls": len(tool_calls),
        "duration_s": round(elapsed, 1),
    }

    if args.response_file:
        with open(args.response_file, "w") as f:
            json.dump(result, f, indent=2)

    print(f"Done. conversation={conversation_id} tool_calls={len(tool_calls)} duration={elapsed:.1f}s", file=sys.stderr)
    return 0


def main():
    parser = argparse.ArgumentParser(description="Bond gateway WebSocket client")
    parser.add_argument("--ws-url", required=True, help="Gateway WebSocket URL")
    parser.add_argument("--message", required=True, help="Message to send")
    parser.add_argument("--agent-id", default="", help="Agent ID (optional)")
    parser.add_argument("--timeout", type=int, default=600, help="Max seconds to wait")
    parser.add_argument("--response-file", default="", help="Output response JSON file")
    parser.add_argument("--events-file", default="", help="Output JSONL events file")
    args = parser.parse_args()

    exit_code = asyncio.run(run(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
