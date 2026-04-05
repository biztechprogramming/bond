# Design Doc 101: Image Generation Frontend UX

**Status:** Proposal  
**Author:** Bond AI  
**Date:** 2026-04-06  
**Depends on:** Design Doc 100 (Image Generation Integrations)  
**Scope:** Frontend-only — chat display, settings tab, lightbox, loading states

---

## 1. Problem Statement

Design Doc 100 delivered the backend: a `generate_image` tool with OpenAI, Replicate, and ComfyUI providers. Images are generated and saved to `.bond/images/`, and `MarkdownMessage.tsx` can render workspace image paths inline.

**What's missing is the full frontend experience:**

| Gap | Impact |
|-----|--------|
| Tool results render as raw JSON, not image cards | Users see `{"paths": ["/workspace/.bond/images/logo.png"]}` instead of the actual image |
| No image lightbox — click opens a new browser tab | Disorienting; no zoom, no metadata, no download button |
| No loading state during generation (5–15s) | Users see generic "⚡ generate_image" with no visual feedback |
| No settings tab for image providers | Users must use CLI wizard or edit YAML to configure providers |
| No cost visibility | Users have no idea what a generation costs or their daily spend |
| No image gallery/history | Previously generated images are buried in conversation history |

### 1.1 What We Studied

We analyzed three open-source projects that handle image generation well:

- **Open WebUI** — Svelte-based, native DALL-E + ComfyUI + Automatic1111 integration
- **LibreChat** — React-based, DALL-E 3 inline rendering with artifact-style cards
- **LobeChat** — React-based, plugin architecture with hover actions and gallery views

Each project solves these problems differently. This doc captures the best patterns from each, adapted to Bond's Next.js + SpacetimeDB architecture.

---

## 2. Goals

1. **Generated images display as rich cards** in the chat — not raw JSON or bare `<img>` tags
2. **Click-to-expand lightbox** with zoom, download, metadata, and regenerate action
3. **Visible loading state** with provider-aware progress indicator during generation
4. **Settings > Images tab** for configuring providers, models, defaults, and budget
5. **Per-image cost badge** and daily spend tracking in the UI
6. **Image gallery** accessible from the Board page for browsing all generated images

## 3. Non-Goals

- Image editing canvas (inpainting, outpainting) — future feature
- Drag-and-drop image upload for img2img — future feature
- Video generation display — out of scope
- Mobile-specific layout — responsive design only, no native mobile

---

## 4. Patterns Borrowed from Open Source

### 4.1 From Open WebUI — Settings Page & Loading States

**What they do:**
- Dedicated `Settings > Images` tab with: provider selector dropdown (OpenAI / ComfyUI / Automatic1111), model picker, API URL field, image size defaults, and steps slider
- Settings are persisted server-side and take effect immediately — no restart required
- During generation: a shimmer/skeleton placeholder appears exactly where the image will render, sized to the requested dimensions. The placeholder pulses with a subtle gradient animation
- Provider logo badge on the placeholder ("Generating with DALL·E 3...")

**What we borrow:**
- The dedicated settings tab layout with provider-as-first-class-selector pattern
- The skeleton placeholder sized to requested dimensions
- Provider logo/name on the loading indicator

**What we adapt:**
- Open WebUI uses Svelte stores; we use SpacetimeDB tables + React state
- Their settings are REST-based; ours persist via SpacetimeDB `settings` table reducers
- They don't have budget controls; we add a daily budget limit field (from Design Doc 081)

### 4.2 From LibreChat — Image Cards & Tool Result Parsing

**What they do:**
- When a DALL-E tool result arrives, they parse the structured response and render an `ImageCard` component instead of raw JSON
- Each card shows: the generated image, the **revised prompt** (DALL-E often rewrites your prompt), the model used, and the image dimensions
- Multiple images display in a responsive 2×2 grid with gap spacing
- Cards have a subtle border, rounded corners, and a shadow — they look like "artifacts" distinct from regular text
- A small metadata bar below the image shows: provider icon, model name, generation time

**What we borrow:**
- Tool result → image card rendering logic: detect `generate_image` in tool results, parse `paths` array, render `<ImageCard>` components
- The revised/enhanced prompt display — show what the model actually generated from
- The 2×2 grid layout for multi-image results
- The metadata bar pattern (provider, model, time)

**What we adapt:**
- LibreChat stores images as base64 in the message; we serve from `/api/v1/workspace-files/` endpoint
- They use a custom `ImageCard` React component; we build ours with Tailwind matching Bond's dark theme
- They don't show cost; we add a cost badge to the metadata bar

### 4.3 From LobeChat — Lightbox & Hover Actions

**What they do:**
- Click an image → full-screen lightbox overlay with smooth fade-in animation
- Lightbox has: zoom in/out buttons, fit-to-screen toggle, download button, copy-to-clipboard button
- **Hover actions** on the image card itself (before clicking): small icon buttons appear in the top-right corner — download, copy prompt, regenerate with same prompt
- Gallery view across conversations: a grid of all generated images with date grouping
- Plugin metadata shown as a collapsible section below the image

**What we borrow:**
- The lightbox overlay with zoom and download
- Hover action buttons on image cards (download, copy prompt, regenerate)
- The gallery concept as a Board sub-page

**What we adapt:**
- LobeChat uses Ant Design components; we use headless/Tailwind
- Their gallery is plugin-scoped; ours scans the `.bond/images/` directory across all conversations
- We skip copy-to-clipboard (images are local files, not URLs) and replace with "open in file manager"

### 4.4 From ChatGPT — The Gold Standard UX

**What they do (for reference):**
- Inline image with subtle 1px border and rounded corners
- "Creating image..." text with a spinning progress indicator below the assistant's text
- After generation: image appears with a fade-in animation
- Click → lightbox with the image centered on a dark backdrop
- Revised prompt shown in a collapsible "prompt used" section
- "Edit" button for inpainting (future for us)

**What we borrow:**
- The fade-in animation on image appearance
- The collapsible "prompt used" section
- The simplicity — one image, clean presentation, no visual clutter

---

## 5. Technical Design

### 5.1 Component Architecture

```
frontend/src/components/
├── chat/
│   ├── ImageCard.tsx              # Single image display card
│   ├── ImageGrid.tsx              # 1-4 image responsive grid layout
│   ├── ImageLightbox.tsx          # Full-screen overlay with zoom/download
│   └── ImageGenerationLoader.tsx  # Skeleton placeholder during generation
├── shared/
│   └── MarkdownMessage.tsx        # (existing) — enhanced to detect image tool results
└── ...

frontend/src/app/
├── settings/
│   └── images/
│       ├── ImagesTab.tsx          # Settings > Images tab
│       └── page.tsx               # Next.js route
├── board/
│   └── gallery/
│       ├── GalleryView.tsx        # Image gallery grid
│       └── page.tsx               # Next.js route
└── ...
```

### 5.2 ImageCard Component

The core display unit. Borrowed from LibreChat's artifact-card pattern, adapted to Bond's dark theme.

```tsx
// frontend/src/components/chat/ImageCard.tsx

interface ImageCardProps {
  src: string;                    // /api/v1/workspace-files/.bond/images/logo.png
  prompt: string;                 // Original prompt text
  revisedPrompt?: string;        // Provider-revised prompt (OpenAI often rewrites)
  provider: string;              // "openai" | "replicate" | "comfyui"
  model: string;                 // "gpt-image-1" | "flux-1.1-pro" | etc.
  size: string;                  // "1024x1024"
  cost?: number;                 // Estimated cost in USD (e.g. 0.04)
  generatedAt: string;           // ISO timestamp
  onExpand: () => void;          // Open lightbox
  onRegenerate?: () => void;     // Re-run with same prompt
}
```

**Visual spec:**
- Container: `rounded-xl border border-zinc-700/50 bg-zinc-800/50 overflow-hidden shadow-lg`
- Image: `w-full aspect-auto object-contain max-h-[512px]` with lazy loading
- Fade-in: CSS `animate-fadeIn` (opacity 0→1 over 300ms) — borrowed from ChatGPT
- Metadata bar below image: provider icon + model name + dimensions + cost badge
- Hover overlay: semi-transparent dark backdrop with action buttons (download, copy prompt, regenerate)

**Metadata bar layout:**
```
┌─────────────────────────────────────────────┐
│  [image content]                            │
├─────────────────────────────────────────────┤
│ 🟢 OpenAI · gpt-image-1 · 1024×1024  $0.04│
│ ▸ Prompt used: "A minimalist app icon..."   │
└─────────────────────────────────────────────┘
```

- Provider indicator: colored dot (🟢 OpenAI, 🟣 Replicate, 🔵 ComfyUI) + name
- Cost badge: `bg-emerald-500/20 text-emerald-400 text-xs px-1.5 py-0.5 rounded-full`
- "Prompt used" is a collapsible `<details>` element — collapsed by default, click to expand (borrowed from ChatGPT)

**Hover actions** (borrowed from LobeChat):
```
┌─────────────────────────────────────────────┐
│                                    [⬇][📋][🔄]│
│  [image with dark overlay on hover]         │
│                                             │
└─────────────────────────────────────────────┘
```
- ⬇ Download — triggers browser download of the image file
- 📋 Copy prompt — copies the generation prompt to clipboard
- 🔄 Regenerate — re-sends the same prompt to the agent (inserts a new user message)

### 5.3 ImageGrid Component

Handles 1–4 images from a single `generate_image` call. Borrowed from LibreChat's grid layout.

```tsx
// frontend/src/components/chat/ImageGrid.tsx

interface ImageGridProps {
  images: ImageCardProps[];
  onExpand: (index: number) => void;
}
```

**Layout rules:**
| Count | Layout | CSS Grid |
|-------|--------|----------|
| 1 | Single image, max-width 512px | `grid-cols-1 max-w-lg` |
| 2 | Side by side | `grid-cols-2 gap-2` |
| 3 | 2 top + 1 bottom (spanning full width) | `grid-cols-2 gap-2` with last item `col-span-2` |
| 4 | 2×2 grid | `grid-cols-2 gap-2` |

### 5.4 ImageLightbox Component

Full-screen overlay. Borrowed from LobeChat's lightbox, simplified.

```tsx
// frontend/src/components/chat/ImageLightbox.tsx

interface ImageLightboxProps {
  src: string;
  prompt: string;
  revisedPrompt?: string;
  provider: string;
  model: string;
  size: string;
  cost?: number;
  onClose: () => void;
  onPrev?: () => void;          // Navigate to previous image (if grid)
  onNext?: () => void;          // Navigate to next image (if grid)
}
```

**Visual spec:**
- Backdrop: `fixed inset-0 z-50 bg-black/80 backdrop-blur-sm` with click-to-close
- Image: centered, `max-w-[90vw] max-h-[85vh] object-contain` with smooth scale-in animation
- Controls bar (bottom): zoom in, zoom out, fit-to-screen, download, close
- Metadata panel (right side or bottom on mobile): prompt, revised prompt, provider, model, size, cost, timestamp
- Keyboard: `Escape` to close, `←/→` for prev/next, `+/-` for zoom
- Transition: `animate-scaleIn` (scale 0.95→1 + opacity 0→1 over 200ms)

### 5.5 ImageGenerationLoader Component

Skeleton placeholder that appears during generation. Borrowed from Open WebUI's shimmer pattern.

```tsx
// frontend/src/components/chat/ImageGenerationLoader.tsx

interface ImageGenerationLoaderProps {
  provider: string;
  model: string;
  size: string;               // Used to set aspect ratio of skeleton
}
```

**Visual spec:**
- Container matches `ImageCard` dimensions based on requested `size`
- Shimmer animation: `bg-gradient-to-r from-zinc-800 via-zinc-700 to-zinc-800 animate-shimmer`
- Center text: "Generating with {provider}/{model}..." in muted text
- Provider-colored pulsing dot indicator
- Below: estimated time remaining based on provider (OpenAI ~5s, Replicate ~10s, ComfyUI ~15-30s)

**Integration with chat page:**
When a `tool_call` event arrives with `name === "generate_image"`, the chat page renders `<ImageGenerationLoader>` in the message stream. When the `tool_result` or subsequent `chunk`/`done` event arrives with image paths, it swaps to `<ImageGrid>`.

### 5.6 Tool Result Detection & Rendering

**Current behavior:** The chat page (`page.tsx`) handles `tool_call` events by adding them to `toolActivity` state as text summaries. Tool results come back as part of the assistant's streamed response (markdown).

**New behavior:** Add a detection layer that intercepts `generate_image` results and renders them as `<ImageGrid>` instead of raw text.

```tsx
// In page.tsx — enhance the message rendering logic

// Detection: when parsing assistant messages, look for image tool result patterns
function extractImageResults(content: string): ImageToolResult | null {
  // Pattern 1: JSON block with generate_image result
  const jsonMatch = content.match(/```json\s*(\{[\s\S]*?"paths"\s*:[\s\S]*?\})\s*```/);
  if (jsonMatch) {
    try {
      const data = JSON.parse(jsonMatch[1]);
      if (data.paths && Array.isArray(data.paths)) return data;
    } catch {}
  }
  
  // Pattern 2: Inline image paths from MarkdownMessage rewriting
  // Already handled by MarkdownMessage.tsx — no change needed
  
  return null;
}

// In the message list renderer:
// If extractImageResults(msg.content) returns data, render <ImageGrid> 
// followed by the remaining text content (with the JSON block stripped out)
```

**Tool call event enhancement:**
```tsx
// In the tool_call handler (around line 271 of page.tsx):
} else if (msg.type === "tool_call" && msg.content) {
  const data = JSON.parse(msg.content);
  const name = data.tool_name || data.name || "tool";
  
  // Special handling for image generation
  if (name === "generate_image") {
    const args = typeof data.args === "string" ? JSON.parse(data.args) : data.args;
    setPendingImageGen({
      provider: args.provider || "openai",
      model: args.model || "default",
      size: args.size || "1024x1024",
      prompt: args.prompt,
    });
  }
  
  // ... existing toolActivity logic
}
```

### 5.7 Settings > Images Tab

New tab in the settings page. Borrowed from Open WebUI's `Settings > Images` layout, adapted to Bond's tab pattern.

**Step 1: Register the tab** in `settings/page.tsx`:
```tsx
const TABS = [
  { id: "agents", label: "Agents" },
  { id: "containers", label: "Container Hosts" },
  { id: "deployment", label: "Deployment" },
  { id: "channels", label: "Channels" },
  { id: "prompts", label: "Prompts" },
  { id: "images", label: "Images" },        // ← NEW
  { id: "llm", label: "LLM" },
  { id: "embedding", label: "Embedding" },
  { id: "api-keys", label: "API Keys" },
  { id: "skills", label: "Skills" },
  { id: "optimization", label: "Optimization" },
] as const;
```

**Step 2: ImagesTab component:**

```tsx
// frontend/src/app/settings/images/ImagesTab.tsx

// Layout (borrowed from Open WebUI's Images settings):
//
// ┌─ Images Settings ─────────────────────────────────────────┐
// │                                                           │
// │  Image Provider                                           │
// │  ┌──────────────────────────────────────────────────┐     │
// │  │ OpenAI (DALL-E / gpt-image-1)              ▾    │     │
// │  └──────────────────────────────────────────────────┘     │
// │                                                           │
// │  Model                                                    │
// │  ┌──────────────────────────────────────────────────┐     │
// │  │ gpt-image-1                                ▾    │     │
// │  └──────────────────────────────────────────────────┘     │
// │                                                           │
// │  Default Resolution                                       │
// │  ○ 256×256  ○ 512×512  ● 1024×1024  ○ 1536×1024         │
// │                                                           │
// │  Quality                                                  │
// │  ● Standard  ○ HD                                         │
// │                                                           │
// │  Style (OpenAI only)                                      │
// │  ● Natural  ○ Vivid                                       │
// │                                                           │
// │  ── Budget Controls ──────────────────────────────────    │
// │                                                           │
// │  Daily Budget Limit                                       │
// │  ┌──────────────────────────────────────────────────┐     │
// │  │ $5.00                                            │     │
// │  └──────────────────────────────────────────────────┘     │
// │  Today's spend: $0.32 / $5.00  ████░░░░░░░ 6.4%          │
// │                                                           │
// │  ── Provider-Specific ────────────────────────────────    │
// │                                                           │
// │  Replicate API Key          [saved ✓] [clear]             │
// │  ┌──────────────────────────────────────────────────┐     │
// │  │ ••••••••••••••••••••                             │     │
// │  └──────────────────────────────────────────────────┘     │
// │                                                           │
// │  ComfyUI Server URL                                       │
// │  ┌──────────────────────────────────────────────────┐     │
// │  │ http://localhost:8188                             │     │
// │  └──────────────────────────────────────────────────┘     │
// │  Status: ● Connected (4 models available)                 │
// │                                                           │
// │  ── Output ───────────────────────────────────────────    │
// │                                                           │
// │  Output Directory                                         │
// │  ┌──────────────────────────────────────────────────┐     │
// │  │ .bond/images                                     │     │
// │  └──────────────────────────────────────────────────┘     │
// │                                                           │
// │                                          [Save Settings]  │
// └───────────────────────────────────────────────────────────┘
```

**Settings persistence:**
All settings save to SpacetimeDB `settings` table using existing `upsert_setting` reducer:
- `image.provider` → `"openai"` | `"replicate"` | `"comfyui"`
- `image.model` → `"gpt-image-1"` | `"black-forest-labs/flux-1.1-pro"` | etc.
- `image.resolution` → `"1024x1024"`
- `image.quality` → `"standard"` | `"hd"`
- `image.style` → `"natural"` | `"vivid"`
- `image.budget_daily` → `"5.00"`
- `image.output_dir` → `".bond/images"`
- `image.comfyui_url` → `"http://localhost:8188"`

API keys save via existing `upsert_provider_api_key` reducer (same pattern as LLM keys).

**Provider-conditional fields:**
- "Style" only shows when provider is OpenAI (it's an OpenAI-specific parameter)
- "ComfyUI Server URL" and connection status only show when provider is ComfyUI
- "Replicate API Key" only shows when provider is Replicate
- Model dropdown options change based on selected provider (loaded from `providers.yaml`)

### 5.8 Cost Display

**Per-image cost badge** (on ImageCard):
- The backend `generate_image` tool result already returns provider and model
- Map to estimated costs:
  - OpenAI gpt-image-1: $0.04 (1024×1024 standard), $0.08 (1024×1024 HD), $0.12 (1536×1536)
  - OpenAI dall-e-3: $0.04 (1024×1024 standard), $0.08 (1024×1024 HD)
  - Replicate flux-1.1-pro: ~$0.05 per image
  - Replicate SDXL: ~$0.01 per image
  - ComfyUI: $0.00 (local)
- Cost map lives in a shared constant: `frontend/src/lib/image-costs.ts`

**Daily spend indicator** (on Settings > Images tab):
- Query backend endpoint: `GET /api/v1/image-generation/spend?period=today`
- Display as progress bar against daily budget limit
- Color: green (<50%), yellow (50-80%), red (>80%)

### 5.9 Image Gallery (Board Sub-Page)

Accessible from Board page navigation. Shows all generated images across conversations.

**Data source:** `GET /api/v1/workspace-files/.bond/images/` — list directory contents with metadata.

**Layout:**
- Masonry grid of image thumbnails grouped by date
- Each thumbnail shows: image, prompt snippet (first 60 chars), provider badge, cost
- Click → opens ImageLightbox
- Filter bar: by provider, by date range, by conversation
- Sort: newest first (default), oldest first, by cost

**Route:** `/board/gallery`

---

## 6. State Management

### 6.1 New State in Chat Page (`page.tsx`)

```tsx
// Pending image generation (for showing loader)
const [pendingImageGen, setPendingImageGen] = useState<{
  provider: string;
  model: string;
  size: string;
  prompt: string;
} | null>(null);

// Lightbox state
const [lightbox, setLightbox] = useState<{
  images: ImageCardProps[];
  currentIndex: number;
} | null>(null);
```

### 6.2 Image Settings (SpacetimeDB)

Use existing `settings` table — no schema changes needed. Settings are read via `useSettings()` hook already available in the frontend.

---

## 7. Event Flow

### 7.1 Happy Path: User Asks for an Image

```
User: "Create a logo for my app"
  │
  ▼
Gateway WS: tool_call { name: "generate_image", args: { prompt: "..." } }
  │
  ▼
Chat page: setPendingImageGen({ provider, model, size, prompt })
           Render <ImageGenerationLoader> in message stream
  │
  ▼
[5-15 seconds pass — agent calls provider API]
  │
  ▼
Gateway WS: chunk (assistant streams response with image paths)
  │
  ▼
Chat page: extractImageResults() detects paths
           setPendingImageGen(null)
           Render <ImageGrid> with <ImageCard> components
           Fade-in animation on the cards
  │
  ▼
User clicks image → setLightbox({ images, currentIndex: 0 })
                     Render <ImageLightbox>
```

### 7.2 Error Path

```
Gateway WS: tool_call { name: "generate_image", ... }
  │
  ▼
Chat page: Show <ImageGenerationLoader>
  │
  ▼
Agent response: "I couldn't generate the image because..."
  │
  ▼
Chat page: setPendingImageGen(null)
           Render error as normal assistant text (no image card)
```

---

## 8. Implementation Phases

### Phase 1 — Image Cards & Tool Detection (Priority: High, Effort: Small)

**Files changed:**
- `frontend/src/components/chat/ImageCard.tsx` — NEW
- `frontend/src/components/chat/ImageGrid.tsx` — NEW
- `frontend/src/app/page.tsx` — Add `extractImageResults()`, render `<ImageGrid>` for image tool results
- `frontend/src/lib/image-costs.ts` — NEW (cost lookup map)

**Definition of done:** When an agent generates an image, it displays as a styled card with metadata bar instead of raw JSON or a bare `<img>` tag. Multiple images show in a grid.

### Phase 2 — Lightbox & Loading State (Priority: Medium, Effort: Small)

**Files changed:**
- `frontend/src/components/chat/ImageLightbox.tsx` — NEW
- `frontend/src/components/chat/ImageGenerationLoader.tsx` — NEW
- `frontend/src/app/page.tsx` — Add `pendingImageGen` state, lightbox state, keyboard handlers

**Definition of done:** Click an image → lightbox with zoom/download. During generation → skeleton loader with provider info.

### Phase 3 — Settings Tab (Priority: High, Effort: Medium)

**Files changed:**
- `frontend/src/app/settings/images/ImagesTab.tsx` — NEW
- `frontend/src/app/settings/images/page.tsx` — NEW
- `frontend/src/app/settings/page.tsx` — Add "images" to TABS array
- `backend/app/api/v1/settings.py` — Add `GET /image-generation/spend` endpoint (if not already covered by cost tracking)

**Definition of done:** Settings > Images tab is functional. Users can select provider, model, resolution, quality, style, budget, and provider-specific config. Settings persist via SpacetimeDB.

### Phase 4 — Gallery (Priority: Low, Effort: Medium)

**Files changed:**
- `frontend/src/app/board/gallery/GalleryView.tsx` — NEW
- `frontend/src/app/board/gallery/page.tsx` — NEW
- `frontend/src/app/board/page.tsx` — Add gallery link in Board navigation
- `backend/app/api/v1/workspace.py` — Add directory listing endpoint for `.bond/images/`

**Definition of done:** `/board/gallery` shows a masonry grid of all generated images with filtering and lightbox.

---

## 9. CSS & Animation Additions

```css
/* Add to globals.css or Tailwind config */

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes scaleIn {
  from { opacity: 0; transform: scale(0.95); }
  to { opacity: 1; transform: scale(1); }
}

@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

.animate-fadeIn { animation: fadeIn 300ms ease-out; }
.animate-scaleIn { animation: scaleIn 200ms ease-out; }
.animate-shimmer {
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}
```

---

## 10. Accessibility

| Requirement | Implementation |
|-------------|---------------|
| Image alt text | Use the generation prompt as `alt` attribute |
| Lightbox keyboard nav | `Escape` close, `←/→` navigate, `Tab` through controls |
| Screen reader | `aria-label` on action buttons, `role="dialog"` on lightbox |
| Reduced motion | Respect `prefers-reduced-motion` — disable animations |
| Color contrast | All text meets WCAG AA (4.5:1 ratio) against dark background |
| Focus management | Trap focus inside lightbox when open; return focus on close |

---

## 11. Testing Strategy

### Unit Tests
- `ImageCard` renders with all props, handles missing optional props gracefully
- `ImageGrid` renders correct grid layout for 1, 2, 3, 4 images
- `extractImageResults()` correctly parses JSON blocks, handles malformed input
- Cost lookup returns correct values for all provider/model combinations

### Integration Tests
- Settings > Images tab: change provider → model dropdown updates
- Settings > Images tab: save settings → SpacetimeDB `settings` table updated
- Chat: send "generate an image" → loader appears → image card renders after tool result
- Lightbox: click image → overlay opens, Escape closes, download works

### Visual Regression
- Screenshot tests for ImageCard in light/dark themes
- Screenshot tests for ImageGrid with 1, 2, 3, 4 images
- Screenshot tests for ImageLightbox

---

## 12. Open Questions

- [ ] Should the lightbox support pinch-to-zoom on touch devices?
- [ ] Should we show a "generating..." toast notification if the user scrolls away from the chat?
- [ ] Should the gallery support bulk download (zip) of selected images?
- [ ] Should image generation history be persisted to SpacetimeDB, or only exist as workspace files?
- [ ] Should we add a "favorite" / "star" feature to pin images in the gallery?

---

## 13. References

- **Design Doc 100** — Image Generation Integrations (backend)
- **Design Doc 081** — Cost Tracking and Budget Controls
- **`frontend/src/components/shared/MarkdownMessage.tsx`** — Current image rendering (workspace path rewriting)
- **`frontend/src/app/settings/page.tsx`** — Settings page with tab registration pattern
- **`frontend/src/app/page.tsx`** — Chat page with tool_call event handling
- **`frontend/src/app/board/page.tsx`** — Board page (gallery will be a sub-page)
- [Open WebUI — Image Settings](https://github.com/open-webui/open-webui) — Settings > Images tab pattern
- [LibreChat — Image Card](https://github.com/danny-avila/LibreChat) — Tool result → image card rendering
- [LobeChat — Image Plugin](https://github.com/lobehub/lobe-chat) — Lightbox, hover actions, gallery
