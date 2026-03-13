# Design Doc 041: Drag-and-Drop Tier Assignment for Prompt Fragments

**Status:** Draft  
**Date:** 2026-03-13  
**Depends on:** 027 (Fragment Selection Roadmap), 028 (Checkbox Removal & Tier Migration)

---

## 1. Problem

The Prompts settings tab displays fragments grouped by tier with colored badges, but there is no way to **change a fragment's tier** from the UI. To reclassify a fragment (e.g. promote a Tier 3 semantic fragment to Tier 2 lifecycle, or demote a Tier 1 always-on fragment to Tier 3), a developer must manually edit `prompts/manifest.yaml`, understand the YAML structure, and restart the backend. This is error-prone and disconnected from the visual tier model the UI already presents.

**Goal:** Let users drag a fragment card from one tier column to another, persisting the change to `manifest.yaml` on disk. No database changes — prompts are files (doc 021).

---

## 2. Design

### 2.1 UI Layout Change: Column-Based Tier View

Replace the current flat list of fragment cards with a **three-column Kanban-style layout**, one column per tier:

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Tier 1          │  │  Tier 2          │  │  Tier 3          │
│  Always-On       │  │  Lifecycle       │  │  Semantic        │
│  ─────────────── │  │  ─────────────── │  │  ─────────────── │
│  ┌─────────────┐ │  │  ┌─────────────┐ │  │  ┌─────────────┐ │
│  │ safety.md   │ │  │  │ reasoning   │ │  │  │ postgresql  │ │
│  └─────────────┘ │  │  │ phase:impl  │ │  │  │ utterances: │ │
│  ┌─────────────┐ │  │  └─────────────┘ │  │  │ "postgres.."│ │
│  │ memory.md   │ │  │  ┌─────────────┐ │  │  └─────────────┘ │
│  └─────────────┘ │  │  │ must-compile│ │  │  ┌─────────────┐ │
│                   │  │  │ phase:impl  │ │  │  │ react.md    │ │
│                   │  │  └─────────────┘ │  │  └─────────────┘ │
└─────────────────┘  └─────────────────┘  └─────────────────┘
```

Each column:
- Has a colored header matching existing `TIER_COLORS` (green, blue, yellow)
- Shows a fragment count and total token estimate
- Is a **drop zone** — dragging a card here changes its tier
- Scrolls independently if the list is long

Each card:
- Is **draggable** via the HTML Drag and Drop API
- Shows the fragment name, path, token estimate, and tier-specific metadata (phase for Tier 2, utterances for Tier 3)
- Retains the existing Edit button and inline editor

### 2.2 Drag-and-Drop Mechanics

**Library choice: None (native HTML5 DnD API).**

The interaction is simple enough — drag a card, drop it in a column — that a library like `dnd-kit` or `react-beautiful-dnd` is unnecessary overhead. The native API covers this with `draggable`, `onDragStart`, `onDragOver`, `onDrop`.

**Interaction flow:**

1. User grabs a fragment card (cursor changes to `grab`)
2. Card becomes semi-transparent; a drag ghost follows the cursor
3. Valid drop columns highlight with a dashed border
4. User drops the card into a different tier column
5. **If moving TO Tier 2:** A modal prompts for the `phase` value (required). Options: `planning`, `implementing`, `reviewing`, `committing`. Cancel aborts the move.
6. **If moving TO Tier 3:** A modal prompts for at least one `utterance` (required for semantic routing). Cancel aborts the move.
7. **If moving TO Tier 1:** No additional metadata needed. Existing `phase` and `utterances` are cleared.
8. Card animates into the new column; a success toast confirms the change.

**State model:**

```typescript
// Drag state tracked in PromptsTab
const [draggedFragment, setDraggedFragment] = useState<string | null>(null); // path
const [dropTarget, setDropTarget] = useState<number | null>(null); // tier number

// On drop, call the new API endpoint
const moveFragment = async (path: string, newTier: number, meta?: { phase?: string; utterances?: string[] }) => {
  await fetch(`${API}/fragments/${path}/tier`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tier: newTier, phase: meta?.phase ?? null, utterances: meta?.utterances ?? [] }),
  });
  await fetchAll(); // Refresh fragment list
};
```

### 2.3 Backend: PATCH Endpoint for Tier Changes

**New endpoint:** `PATCH /api/v1/prompts/fragments/{fragment_path:path}/tier`

```python
class TierUpdate(BaseModel):
    tier: int  # 1, 2, or 3
    phase: str | None = None  # Required if tier == 2
    utterances: list[str] = []  # Required if tier == 3

@router.patch("/fragments/{fragment_path:path}/tier")
async def update_fragment_tier(fragment_path: str, body: TierUpdate):
    """Move a fragment to a different tier by updating manifest.yaml."""
    if body.tier not in (1, 2, 3):
        raise HTTPException(400, "Tier must be 1, 2, or 3")
    if body.tier == 2 and not body.phase:
        raise HTTPException(400, "Phase is required for Tier 2 fragments")
    if body.tier == 3 and not body.utterances:
        raise HTTPException(400, "At least one utterance is required for Tier 3 fragments")

    prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts"
    manifest_path = prompts_dir / "manifest.yaml"

    # Read, modify, write manifest.yaml
    raw = yaml.safe_load(manifest_path.read_text())
    if fragment_path not in raw:
        raise HTTPException(404, "Fragment not in manifest")

    entry: dict = {"tier": body.tier}
    if body.tier == 2:
        entry["phase"] = body.phase
    if body.tier == 3:
        entry["utterances"] = body.utterances
    # Tier 1 gets only {"tier": 1}

    raw[fragment_path] = entry
    manifest_path.write_text(yaml.dump(raw, default_flow_style=False, sort_keys=False))

    # Invalidate the in-memory manifest cache
    from backend.app.agent.manifest import invalidate_cache
    invalidate_cache()

    return {"path": fragment_path, "tier": body.tier, "status": "moved"}
```

**Validation rules:**
| Target Tier | Required Fields | Cleared Fields |
|---|---|---|
| 1 (Always-on) | None | `phase`, `utterances` |
| 2 (Lifecycle) | `phase` (one of: `planning`, `implementing`, `reviewing`, `committing`) | `utterances` |
| 3 (Semantic) | `utterances` (≥1 string) | `phase` |

### 2.4 Manifest YAML Preservation

`manifest.yaml` uses section comments (`# Tier 1: Always On`, etc.) for readability. A naive `yaml.dump()` would destroy these comments.

**Approach: Use `ruamel.yaml` instead of `PyYAML` for round-trip editing.**

`ruamel.yaml` preserves comments, ordering, and formatting. The PATCH endpoint should:
1. Load with `ruamel.yaml.YAML(typ='rt')` (round-trip mode)
2. Remove the fragment entry from its current position
3. Insert it into the correct tier section (after the last entry of that tier)
4. Write back — comments and structure are preserved

If `ruamel.yaml` is not already a dependency, add it: `uv add ruamel.yaml`. It's a pure-Python drop-in that handles YAML round-tripping correctly.

**Fallback:** If comment preservation proves too fragile, accept that `yaml.dump()` strips comments and regenerate section headers programmatically by grouping entries by tier before writing. This is less elegant but reliable.

### 2.5 Tier-Specific Metadata Modals

When a fragment is dropped into Tier 2 or Tier 3, a modal collects required metadata before the move is committed.

**Tier 2 Modal (Phase Selection):**
```
┌──────────────────────────────────┐
│  Move to Tier 2: Lifecycle       │
│  ────────────────────────────── │
│  Select the lifecycle phase:     │
│                                  │
│  ○ planning                      │
│  ● implementing                  │
│  ○ reviewing                     │
│  ○ committing                    │
│                                  │
│  [Cancel]            [Confirm]   │
└──────────────────────────────────┘
```

**Tier 3 Modal (Utterances):**
```
┌──────────────────────────────────┐
│  Move to Tier 3: Semantic        │
│  ────────────────────────────── │
│  Add trigger utterances:         │
│                                  │
│  ┌──────────────────────┐ [Add]  │
│  │ PostgreSQL query      │       │
│  └──────────────────────┘       │
│                                  │
│  • "PostgreSQL query"        ✕   │
│  • "database optimization"   ✕   │
│                                  │
│  [Cancel]            [Confirm]   │
└──────────────────────────────────┘
```

Both modals:
- Block the drop until confirmed or cancelled
- Pre-populate with existing values if the fragment already has them (e.g. moving a Tier 2 fragment with `phase: implementing` to Tier 3 — the phase is shown as context but utterances must be added)
- Validate before allowing Confirm (phase must be selected; ≥1 utterance required)

---

## 3. Component Structure

```
PromptsTab.tsx
├── TierColumn (×3)
│   ├── column header (tier label, color, count, token total)
│   ├── drop zone handlers (onDragOver, onDrop)
│   └── FragmentCard (×N per tier)
│       ├── draggable div
│       ├── fragment name, path, tokens
│       ├── tier-specific metadata display
│       └── Edit button → inline editor (existing)
├── TierMoveModal
│   ├── phase radio group (Tier 2)
│   └── utterance input + tag list (Tier 3)
└── existing template sub-tab (unchanged)
```

**No new files.** The column layout and modal are small enough to live in `PromptsTab.tsx`. Extract to separate files only if the component exceeds ~500 lines after implementation.

---

## 4. Edge Cases & Guardrails

| Scenario | Behavior |
|---|---|
| Drop onto the same tier | No-op, no API call |
| Drop cancelled (modal dismissed) | Card returns to original position, no state change |
| Fragment has no file on disk | Should not appear in the list (manifest loader skips missing files) |
| Concurrent edits to manifest.yaml | Last-write-wins. Acceptable for single-user local tool. |
| Very long utterance list (Tier 3) | Scrollable tag list in modal; no hard limit |
| Drag while inline editor is open | Disable dragging on the card being edited (`draggable={!isEditing}`) |
| API failure on tier move | Toast error, card snaps back, fragment list re-fetched |
| Moving a Tier 1 fragment out | Warn: "This fragment will no longer be included in every request. Continue?" |

---

## 5. Accessibility

- Drag handle has `aria-label="Drag to change tier"`
- Drop zones have `aria-label="Drop here to move to Tier N"`
- Keyboard alternative: each card gets a "Move to..." dropdown menu (`<select>`) as a fallback for users who cannot drag. This triggers the same modal flow.
- Focus management: after a successful move, focus moves to the card in its new column
- Screen reader announcement: `aria-live="polite"` region announces "Fragment X moved to Tier N"

---

## 6. Sequencing

| Step | What | Effort |
|---|---|---|
| 1 | Add `PATCH /fragments/{path}/tier` endpoint with validation | ~1 hour |
| 2 | Switch manifest read/write to `ruamel.yaml` for comment preservation | ~1 hour |
| 3 | Refactor `PromptsTab.tsx` from flat list to three-column layout | ~2 hours |
| 4 | Add native HTML5 drag-and-drop handlers | ~1 hour |
| 5 | Build `TierMoveModal` for phase/utterance collection | ~1 hour |
| 6 | Wire drop → modal → API → refresh cycle | ~1 hour |
| 7 | Add keyboard fallback (Move to... dropdown) | ~30 min |
| 8 | Manual QA: drag between all tier combinations, cancel flows, error states | ~1 hour |

**Total estimate: ~8.5 hours**

---

## 7. What This Does NOT Include

- **Reordering within a tier** — fragments are sorted alphabetically by path. If intra-tier ordering becomes important, that's a separate feature requiring an `order` field in the manifest.
- **Creating new fragments** — this feature only moves existing fragments between tiers. Fragment creation is a separate workflow (create file on disk + add manifest entry).
- **Deleting fragments** — deletion means removing from manifest and optionally deleting the file. Out of scope.
- **Bulk operations** — no multi-select drag. Move one fragment at a time.
- **Git auto-commit** — manifest changes are written to disk but not auto-committed. The user manages their own git workflow for `prompts/manifest.yaml`.

---

## 8. Decisions

| Question | Decision |
|---|---|
| DnD library? | **None** — native HTML5 API. Simple enough interaction. |
| New component files? | **No** — keep in `PromptsTab.tsx` unless it exceeds ~500 lines |
| Manifest format preservation? | **`ruamel.yaml`** round-trip mode to preserve comments |
| Tier 1 removal warning? | **Yes** — confirmation dialog before removing from always-on |
| Auto-commit manifest changes? | **No** — user manages git |
| Phase values hardcoded? | **Yes** — `planning`, `implementing`, `reviewing`, `committing` match doc 024 |

---

## 9. Open Questions

1. **Should we allow custom phase values for Tier 2?** Currently hardcoded to four phases from doc 024. If the lifecycle system expands, the modal would need a free-text option.
2. **Should the manifest be re-organized on write?** When moving a fragment, should it be physically relocated in the YAML file to sit under the correct tier section header, or just have its `tier` value changed in-place? Relocation is cleaner for humans reading the file; in-place is safer for tooling.
3. **Token budget visibility?** Should the Tier 1 column show a running total with a warning when always-on tokens exceed a threshold (e.g. 2000 tokens)? This would help users avoid bloating the system prompt.
