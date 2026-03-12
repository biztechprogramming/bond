# Agent Loop Cost Analysis — Pseudocode

## The Loop (what Langfuse shows you)

```
agent-turn-xxx-iter-8 (trace)
└── 23 litellm-acompletion calls (all Opus)
```

Here's what that means in pseudocode:

```python
def agent_turn(user_message, history):
    messages = [system_prompt, ...history, user_message]
    
    for iteration in range(max_iterations):     # iter-0 through iter-N
        
        # ┌──────────────────────────────────────────────┐
        # │  THIS IS ONE litellm-acompletion CALL        │
        # │                                              │
        # │  Sends ALL messages so far to Opus           │
        # │  Input: system_prompt + full history +       │
        # │         all prior tool calls & results       │
        # │  Output: text and/or tool_calls              │
        # │                                              │
        # │  COST = (all input tokens) + (output tokens) │
        # │  Each iteration, input grows because we      │
        # │  keep appending tool results                 │
        # └──────────────────────────────────────────────┘
        
        response = opus(messages, tools)          # $$$
        
        if response.has_tool_calls:
            for tool_call in response.tool_calls:
                result = execute_tool(tool_call)  # free (local execution)
                messages.append(tool_call)         # grows the context
                messages.append(result)            # grows the context MORE
            continue  # → next iteration, send EVERYTHING again to Opus
        
        else:
            return response.text  # done — this is the reply to the user
```

## Why 23 Opus calls is expensive

Each call sends the FULL conversation so far. The context snowballs:

```
iter-0:  [system_prompt + user_msg]                    →  Opus  →  "read 3 files"
iter-1:  [system_prompt + user_msg + 3 file contents]  →  Opus  →  "read 2 more"
iter-2:  [... + 2 more file contents]                  →  Opus  →  "grep for X"
iter-3:  [... + grep results]                          →  Opus  →  "read 1 more"
...
iter-22: [system_prompt + user_msg + ALL prior tool    →  Opus  →  "here's your answer"
          calls + ALL results (possibly 100K+ tokens)]
```

**iter-0 might cost $0.02. iter-22 might cost $0.50.** Same model, but the input
has grown 25x because every prior tool call and result is still in the context.

The total cost is roughly: N × average_context_size × opus_input_price
For 23 calls with growing context, you're paying for the TRIANGLE:

    $
    |          ╱
    |        ╱
    |      ╱
    |    ╱
    |  ╱
    |╱__________
     iter 0   22

Each iteration re-sends everything before it. The area under that line is your bill.


## What's happening in those 23 calls (typical pattern)

```
Call  1: Opus reads user prompt, calls file_read × 3      (exploring)
Call  2: Opus sees file contents, calls file_read × 2     (more exploring)
Call  3: Opus calls shell_grep                             (searching)
Call  4: Opus calls file_read × 1                          (one more file)
Call  5: Opus calls file_read × 1                          (SINGLE read — wasteful!)
Call  6: Opus calls file_read × 1                          (SINGLE read — wasteful!)
Call  7: Opus calls file_read × 1                          (SINGLE read — wasteful!)
...maybe 10 iterations of one-at-a-time reads...
Call 15: Opus finally starts writing code (file_edit)
Call 16: Opus calls file_edit on another file
...
Call 20: Opus calls shell (run tests)
Call 21: Opus sees test failure, calls file_edit (fix)
Call 22: Opus calls shell (run tests again)
Call 23: Opus returns final answer
```

## The three problems

### Problem 1: Single-tool iterations (the big one)
Opus reads ONE file per iteration when it could batch 5.
Each extra iteration = resending the entire context to Opus.
Bond already nudges after 3 consecutive singles, but it still happens.

    WASTEFUL:                           EFFICIENT:
    iter-1: read(file_a)               iter-1: read(file_a)
    iter-2: read(file_b)                       read(file_b)
    iter-3: read(file_c)                       read(file_c)
    iter-4: read(file_d)                       read(file_d)
    = 4 Opus calls                     = 1 Opus call
    = 4× context resent               = 1× context sent

### Problem 2: Opus doing information gathering
Reading files, grepping, exploring — this is commodity work.
Sonnet or Flash could do it at 1/15th the cost and 3× the speed.
Opus should only engage for judgment, planning, and complex edits.

    CURRENT:                            BETTER:
    Opus: read(file_a)     $0.05       Flash: read(file_a, b, c, d)  $0.002
    Opus: read(file_b)     $0.08       Opus: [sees all 4 results]    $0.05
    Opus: read(file_c)     $0.11       Opus: plan + edit             $0.06
    Opus: read(file_d)     $0.14       Total: ~$0.11
    Opus: plan             $0.17
    Opus: edit             $0.20
    Total: ~$0.75

### Problem 3: Opus gathering context for Claude Code
If the task ends with spawning a coding agent, then ALL the file reading
Opus did was just to understand the task — and Claude Code will re-read
those same files anyway.

    CURRENT:
    Opus: read 10 files (10 iterations, $0.50)
    Opus: "ok I understand, spawning claude code"
    Claude Code: reads same 10 files again + more
    Total info-gathering cost: $0.50 + Claude Code's reads

    BETTER:
    Opus: read user prompt, think about approach (1 iteration, $0.02)
    Opus: write detailed task prompt referencing file PATHS (1 iteration, $0.04)
    Opus: spawn claude code with that task
    Claude Code: reads the files it needs
    Total info-gathering cost: $0.06 + Claude Code's reads


## What the agent could do differently

### For tasks it handles itself (no coding agent):
- Batch all info-gathering into fewer iterations
- Use a cheaper model for the read/grep/search phase
- Switch to Opus only when it's time for judgment or edits

### For tasks it delegates to a coding agent:
- DON'T read all the files — just enough to understand the task
- Write a detailed task prompt with file paths, not file contents
- Spawn early, before burning 15 iterations on exploration

### The ideal loop:
User: "Add webhook support"
  iter-0: Opus reads prompt → calls file_read on 2-3 key files     $0.03
  iter-1: Opus understands architecture → writes task spec          $0.05
  iter-2: Opus spawns coding_agent with detailed spec               $0.06
  [Claude Code does the work]
  Total Opus cost: ~$0.14

### vs what often happens:
User: "Add webhook support"
  iter-0 to iter-12: Opus reads files one at a time                 $1.20
  iter-13: Opus starts writing code itself or spawns coding agent   $0.15
  iter-14 to iter-22: more work                                     $0.80
  Total Opus cost: ~$2.15
```
