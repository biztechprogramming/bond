# Design Doc 104: Agent Image Delivery

**Status:** Proposal  
**Author:** Bond AI  
**Date:** 2026-04-06  
**Depends on:** Design Doc 100 (Image Generation Integrations), Design Doc 102 (Image Generation Frontend UX)  
**Scope:** Agent-to-gateway image transport — eliminates filesystem coupling between containers

---

## 1. Problem Statement

The current image generation pipeline relies on **shared volume mounts** between the agent container and the gateway/frontend to deliver generated images to the user. This is fundamentally broken for any deployment where agent containers don't run on the same host as the gateway.

### 1.1 Current Flow (Broken)

```
Agent Container                   Host Filesystem              Gateway/Frontend
┌─────────────────┐              ┌──────────────┐             ┌──────────────────┐
│ generate_image() │              │              │             │                  │
│ saves to         │──volume──▶  │ ~/.bond/      │──volume──▶ │ reads from       │
│ /data/images/    │   mount     │   images/     │   mount    │ ~/.bond/images/  │
└─────────────────┘              └──────────────┘             └──────────────────┘
```

**This requires all three components to share a filesystem.** It fails when:

| Scenario | Why It Fails |
|----------|-------------|
| Agent runs on a remote Docker host | Volume mount is on the remote host, not the gateway host |
| Agent runs in a cloud container (ECS, Cloud Run, ACI) | No shared filesystem at all |
| Multiple gateway instances behind a load balancer | Only one gateway instance has the volume mount |
| Agent runs on a different machine than the user | `~/.bond/images/` doesn't exist on the agent's host in a meaningful way |

### 1.2 Additional Problems with Current Design

| Problem | Impact |
|---------|--------|
| **Naming collisions** — all agents write to the same flat `/data/images/` directory | Two agents generating images from similar prompts overwrite each other |
| **No cleanup** — images accumulate forever | Disk usage grows unbounded |
| **Path translation fragility** — gateway must map `/data/images/` → `~/.bond/images/` | Three different path conventions across agent, gateway, and frontend, connected by string manipulation |
| **Frontend URL mismatch** — `MarkdownMessage.tsx` must rewrite paths, `next.config.ts` must proxy to gateway | Multiple layers of URL rewriting, each a potential point of failure |

---

## 2. Goals

1. **Decouple image delivery from shared filesystems** — agent containers must not rely on volume mounts to deliver images to users
2. **Agent pushes images over HTTP** — the agent uploads generated images to the gateway via an API endpoint
3. **Gateway stores and serves images** — single source of truth, accessible to all frontends
4. **Unique, collision-free naming** — no two agents can overwrite each other's images
5. **Images are associated with conversations** — queryable, cleanable, and attributable to a specific agent and conversation
6. **Frontend serves images from a single, stable URL pattern** — no path rewriting gymnastics

## 3. Non-Goals

- Image editing, transformation, or thumbnailing at the gateway (future)
- External blob storage (S3, Azure Blob) — design should allow it, but initial implementation uses local gateway storage
- Image deduplication or content-addressable storage
- Streaming partial images during generation

---

## 4. Proposed Architecture

### 4.1 New Flow

```
Agent Container                        Gateway                          Frontend
┌─────────────────┐                   ┌──────────────────┐             ┌─────────────┐
│ generate_image() │                   │                  │             │             │
│ saves locally    │                   │                  │             │             │
│ /data/images/    │──HTTP POST───▶   │ POST /api/v1/    │             │             │
│                  │  multipart/       │   images/upload  │             │             │
│                  │  form-data        │                  │             │             │
│                  │                   │ stores to        │             │             │
│                  │◀──200 {url}────  │ /data/images/    │──serves──▶ │ <img src=   │
│                  │                   │ {agent}/{id}.png │             │  "/api/v1/  │
│ returns url in   │                   │                  │             │  images/    │
│ tool response    │                   │ GET /api/v1/     │◀──fetch──  │  {id}.png"  │
└─────────────────┘                   │   images/{id}    │             │  />         │
                                      └──────────────────┘             └─────────────┘
```

**Key change:** The agent **uploads** the image to the gateway over HTTP. No shared filesystem required. The gateway returns a URL that the agent includes in its response. The frontend renders that URL directly — no path rewriting needed.

### 4.2 Gateway Upload Endpoint

```
POST /api/v1/images/upload
Content-Type: multipart/form-data

Fields:
  file:            <binary image data>
  agent_id:        "bond-abc123"
  conversation_id: "conv-xyz789"
  filename:        "bond_magnifying_glass_3d_render.png"
  prompt:          "Bond logo, magnifying glass, 3D render"  (optional, for metadata)

Response:
  201 Created
  {
    "id": "img_a8f3b2c1e4d5",
    "url": "/api/v1/images/img_a8f3b2c1e4d5.png",
    "filename": "bond_magnifying_glass_3d_render.png",
    "size": 245832,
    "mime": "image/png",
    "agent_id": "bond-abc123",
    "conversation_id": "conv-xyz789",
    "created_at": "2026-04-06T12:34:56Z"
  }
```

### 4.3 Gateway Serve Endpoint

```
GET /api/v1/images/{id}.{ext}

Response:
  200 OK
  Content-Type: image/png
  Cache-Control: public, max-age=31536000, immutable
  <binary image data>
```

Images are immutable once uploaded — aggressive caching is safe.

### 4.4 Image Storage on Gateway

```
$BOND_HOME/images/
  ├── img_a8f3b2c1e4d5.png
  ├── img_b7e2c9d0f1a3.png
  └── ...
```

- **Flat directory with UUID-based filenames** — no collisions, no namespacing needed at the filesystem level
- Agent ID and conversation ID are stored in **metadata** (SpacetimeDB or a sidecar JSON file), not the file path
- Original filename preserved in metadata for display purposes

### 4.5 Metadata Storage

Option A — **SpacetimeDB table** (preferred, consistent with Bond's architecture):

```rust
#[spacetimedb::table(name = generated_image, public)]
pub struct GeneratedImage {
    #[primary_key]
    pub id: String,
    pub agent_id: String,
    pub conversation_id: String,
    pub filename: String,
    pub prompt: String,
    pub mime_type: String,
    pub size_bytes: u64,
    pub provider: String,       // "openai", "replicate", "comfyui"
    pub model: String,          // "dall-e-3", "flux-1.1-pro", etc.
    pub created_at: Timestamp,
}
```

Option B — **Sidecar JSON** (simpler, no schema change):

```
$BOND_HOME/images/
  ├── img_a8f3b2c1e4d5.png
  ├── img_a8f3b2c1e4d5.json   # {"agent_id": "...", "conversation_id": "...", ...}
  └── ...
```

### 4.6 Agent-Side Changes (`image_gen.py`)

After generating the image locally, the agent uploads it to the gateway:

```python
async def _upload_to_gateway(file_path: Path, agent_id: str, conversation_id: str, prompt: str) -> dict:
    """Upload a generated image to the gateway and return the image metadata."""
    gateway_url = os.environ.get("GATEWAY_URL", "http://gateway:18789")
    
    async with aiohttp.ClientSession() as session:
        data = aiohttp.FormData()
        data.add_field("file", open(file_path, "rb"), filename=file_path.name)
        data.add_field("agent_id", agent_id)
        data.add_field("conversation_id", conversation_id)
        data.add_field("prompt", prompt)
        
        async with session.post(f"{gateway_url}/api/v1/images/upload", data=data) as resp:
            resp.raise_for_status()
            return await resp.json()
```

The tool response changes from returning a local path to returning the gateway URL:

```python
# BEFORE
return {"paths": ["/data/images/bond_magnifying_glass.png"]}

# AFTER
return {"url": "/api/v1/images/img_a8f3b2c1e4d5.png", "id": "img_a8f3b2c1e4d5"}
```

### 4.7 Frontend Changes

With the gateway serving images at `/api/v1/images/{id}.png`, the frontend needs:

1. **Next.js rewrite** to proxy `/api/v1/*` to the gateway (needed regardless — this is currently missing):
   ```ts
   { source: "/api/v1/:path*", destination: `${gatewayUrl}/api/v1/:path*` }
   ```

2. **Remove path rewriting logic** from `MarkdownMessage.tsx` and `image-utils.ts` — URLs are already correct as returned by the agent.

3. **Image gallery** (Design Doc 102) can query the SpacetimeDB `generated_image` table directly for browsing all images across conversations.

---

## 5. Migration Path

### Phase 1: Add Upload Endpoint (backward-compatible)

1. Add `POST /api/v1/images/upload` to the gateway
2. Add `GET /api/v1/images/{id}.{ext}` to the gateway
3. Add the Next.js rewrite for `/api/v1/*`
4. Both old (volume mount) and new (upload) paths work simultaneously

### Phase 2: Switch Agent to Upload

1. Update `image_gen.py` to upload after generating
2. Agent returns gateway URLs instead of local paths
3. Volume mount still works as fallback for any old conversations

### Phase 3: Remove Volume Mount Dependency

1. Remove `/data/images/` volume mount from `docker-compose.yml`
2. Remove path translation logic from gateway `server.ts` (lines 394-398)
3. Remove `rewriteImageSrc()` path matching for `/data/images/` and `.bond/images/`
4. Clean up `image-utils.ts`

---

## 6. Multi-Agent Considerations

| Concern | Solution |
|---------|----------|
| **Naming collisions** | UUID-based IDs (`img_a8f3b2c1`) — impossible to collide |
| **Attribution** | `agent_id` stored in metadata — always know which agent generated what |
| **Conversation scoping** | `conversation_id` in metadata — gallery can filter by conversation |
| **Cleanup** | Delete images when conversation is deleted, or by age/size policy |
| **Access control** | Gateway can enforce that agents only see their own images (future) |

---

## 7. Future Extensions

- **External storage backends** — S3, Azure Blob, GCS. The gateway upload endpoint stays the same; only the storage implementation changes behind it.
- **Thumbnails** — Gateway generates thumbnails on upload for faster gallery rendering.
- **Content-addressable deduplication** — Hash-based storage to avoid storing identical images twice.
- **Signed URLs with expiry** — For security-sensitive deployments where images shouldn't be permanently accessible.
- **Streaming upload** — Agent streams image bytes as they're generated (for providers that support progressive rendering).

---

## 8. Open Questions

1. **Should the gateway accept images from any agent, or require authentication?** Currently the gateway trusts agents on the internal network. For remote agents, we'll need auth on the upload endpoint.
2. **What's the retention policy?** Images could accumulate quickly. Options: per-conversation cleanup, TTL-based expiry, or user-managed deletion.
3. **Should we store images in SpacetimeDB as blobs?** This would eliminate the filesystem entirely but may not be practical for large images. Better to keep files on disk/blob storage and metadata in SpacetimeDB.
4. **Max image size limit?** DALL-E 3 generates ~1-4MB images. Replicate models can go higher. Need a reasonable upload limit (e.g., 20MB).
