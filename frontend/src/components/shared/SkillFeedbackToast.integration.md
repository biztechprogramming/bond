# SkillFeedbackToast Integration Guide

## 1. Add state to `page.tsx`

```tsx
import SkillFeedbackStack, { type SkillActivation } from "@/components/shared/SkillFeedbackToast";

// Inside Home():
const [skillActivations, setSkillActivations] = useState<SkillActivation[]>([]);
```

## 2. Handle `skill_activated` WebSocket messages

In the existing `onMessage` handler (around line 196):

```tsx
} else if (msg.type === "skill_activated" && msg.content) {
  try {
    const data = JSON.parse(msg.content);
    setSkillActivations((prev) => [
      ...prev,
      {
        id: data.id,
        skillName: data.skillName,
        skillSource: data.skillSource,
        activatedAt: data.activatedAt || Date.now(),
      },
    ]);
  } catch { /* ignore parse errors */ }
}
```

## 3. Add feedback + dismiss handlers

```tsx
const handleSkillFeedback = useCallback((activationId: string, vote: "up" | "down") => {
  wsRef.current?.send(JSON.stringify({
    type: "skill_feedback",
    activationId,
    vote,
  }));
}, []);

const handleSkillDismiss = useCallback((activationId: string) => {
  setSkillActivations((prev) => prev.filter((a) => a.id !== activationId));
}, []);
```

## 4. Render the toast stack

At the end of the JSX return, before the closing `</>`:

```tsx
<SkillFeedbackStack
  activations={skillActivations}
  onFeedback={handleSkillFeedback}
  onDismiss={handleSkillDismiss}
/>
```

## 5. Add `skill_activated` to GatewayMessage type

In `frontend/src/lib/ws.ts`, add to the type union:

```tsx
export interface GatewayMessage {
  type: "response" | "chunk" | ... | "skill_activated";
  // ...
}
```

## 6. Backend: Emit `skill_activated` from the agent loop

When the agent reads a skill's full SKILL.md (the L2 load), emit:

```python
await ws.send_json({
    "type": "skill_activated",
    "content": json.dumps({
        "id": f"act_{uuid4().hex[:12]}",
        "skillName": skill.name,
        "skillSource": skill.source,
        "activatedAt": int(time.time() * 1000),
    })
})
```

## 7. Backend: Handle `skill_feedback` messages

When the gateway receives a `skill_feedback` message:

```python
async def handle_skill_feedback(data: dict):
    activation_id = data["activationId"]
    vote = data["vote"]  # "up" or "down"
    
    # Update skill_usage table
    await db.execute(
        "UPDATE skill_usage SET user_vote = ?, voted_at = ? WHERE id = ?",
        (vote, datetime.utcnow(), activation_id)
    )
    
    # Update aggregated skill_scores
    skill_id = await db.fetchval(
        "SELECT skill_id FROM skill_usage WHERE id = ?", activation_id
    )
    if skill_id:
        col = "thumbs_up" if vote == "up" else "thumbs_down"
        await db.execute(
            f"UPDATE skill_scores SET {col} = {col} + 1, updated_at = ? WHERE skill_id = ?",
            (datetime.utcnow(), skill_id)
        )
        await recalculate_skill_score(skill_id)
```
