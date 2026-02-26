# Design Doc 006: Web Search

**Status**: Draft — awaiting review  
**Author**: Developer Agent  
**Date**: 2026-02-25

## Problem

Bond has `web_search` in its tool list but it's a stub returning "coming in Phase 2." Web search is critical — the agent needs to look things up, read documentation, verify facts, and find solutions to problems.

## Reference: agent-zero

agent-zero uses a layered approach:
1. **SearXNG** (primary) — self-hosted meta-search engine, no API key needed
2. **DuckDuckGo** (fallback) — Python library, no API key
3. **Perplexity** (optional) — AI-powered search via API key

The search results are title + URL + snippet. The agent can then use a browser tool to read full pages.

## Goals

- Web search works out of the box with zero configuration
- Optional upgrade path for better results (API keys)
- Page content extraction (read a URL)
- Results include enough context to be useful without reading every page
- Local-first: SearXNG runs in Docker alongside Bond

## Non-Goals

- Full browser automation (Phase 4)
- JavaScript rendering for search results
- Image/video search
- Real-time search (news within minutes)

## Architecture

### Search Providers (Priority Order)

| Provider | Requires | Quality | Speed | Rate Limits |
|----------|----------|---------|-------|-------------|
| SearXNG | Docker (self-hosted) | ★★★★★ | Fast | None (self-hosted) |
| DuckDuckGo | Nothing | ★★★☆☆ | Fast | Informal (may throttle) |
| Perplexity | API key ($) | ★★★★★ | Medium | Per plan |
| Tavily | API key ($) | ★★★★☆ | Fast | 1000 free/month |

### Provider Selection

```
if SearXNG is running → use SearXNG
else if DuckDuckGo available → use DuckDuckGo
else → return error "No search provider available"

if Perplexity key set → available as "deep_search" mode
if Tavily key set → available as alternative
```

Zero config: DuckDuckGo always works (Python library, no API, no Docker).  
Recommended: Add SearXNG via `docker-compose.dev.yml` for best results.

### Two Tools, Not One

| Tool | Purpose | When to use |
|------|---------|-------------|
| `web_search` | Search the web, get snippets | Finding information, looking up docs, researching |
| `web_read` | Fetch and extract content from a URL | Reading a specific page the agent found or the user provided |

This matches how humans work: search → scan results → read the relevant page.

## Design

### 1. web_search Tool

**Input**:
```json
{
  "query": "MinIO docker compose credentials setup",
  "num_results": 10,
  "provider": "auto"
}
```

**Output**:
```json
{
  "results": [
    {
      "title": "MinIO Docker Quickstart Guide",
      "url": "https://min.io/docs/minio/container/index.html",
      "snippet": "Set MINIO_ROOT_USER and MINIO_ROOT_PASSWORD environment variables to configure credentials..."
    }
  ],
  "provider": "searxng",
  "query": "MinIO docker compose credentials setup"
}
```

**Provider implementations**:

```python
# SearXNG — HTTP POST to local instance
async def search_searxng(query: str, num_results: int = 10) -> list[SearchResult]:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            "http://localhost:8888/search",
            data={"q": query, "format": "json"},
        ) as resp:
            data = await resp.json()
            return [
                SearchResult(
                    title=r["title"],
                    url=r["url"],
                    snippet=r["content"],
                )
                for r in data["results"][:num_results]
            ]

# DuckDuckGo — Python library
async def search_ddg(query: str, num_results: int = 10) -> list[SearchResult]:
    from duckduckgo_search import DDGS
    results = await asyncio.to_thread(
        lambda: DDGS().text(query, max_results=num_results)
    )
    return [
        SearchResult(title=r["title"], url=r["href"], snippet=r["body"])
        for r in results
    ]

# Perplexity — OpenAI-compatible API
async def search_perplexity(query: str) -> list[SearchResult]:
    # Returns AI-synthesized answer with citations
    ...
```

### 2. web_read Tool

**Input**:
```json
{
  "url": "https://min.io/docs/minio/container/index.html",
  "max_length": 5000
}
```

**Output**:
```json
{
  "url": "https://min.io/docs/minio/container/index.html",
  "title": "MinIO Docker Quickstart Guide",
  "content": "extracted markdown text...",
  "length": 4832
}
```

**Implementation**: Use `httpx` + `readability-lxml` (or `trafilatura`) for content extraction. Strip navigation, ads, scripts. Convert to clean markdown. Truncate to `max_length`.

```python
async def read_url(url: str, max_length: int = 5000) -> WebContent:
    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        resp = await client.get(url, headers={"User-Agent": "Bond/1.0"})
        resp.raise_for_status()

    # Extract main content
    from trafilatura import extract
    content = await asyncio.to_thread(
        extract, resp.text, include_links=True, include_tables=True
    )

    if not content:
        # Fallback: basic HTML stripping
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        content = soup.get_text(separator="\n", strip=True)

    # Truncate
    if len(content) > max_length:
        content = content[:max_length] + "\n\n[Content truncated]"

    return WebContent(url=url, title=title, content=content, length=len(content))
```

### 3. SearXNG Docker Service

Add to `docker-compose.dev.yml`:

```yaml
searxng:
  image: searxng/searxng:latest
  container_name: bond-searxng
  ports:
    - "8888:8080"
  environment:
    - SEARXNG_BASE_URL=http://localhost:8888
  volumes:
    - ./docker/searxng/settings.yml:/etc/searxng/settings.yml:ro
  restart: unless-stopped
```

**SearXNG settings** (`docker/searxng/settings.yml`):
```yaml
use_default_settings: true
server:
  secret_key: "bond-searxng-local"
  bind_address: "0.0.0.0"
  port: 8080
search:
  safe_search: 0
  autocomplete: ""
  default_lang: "en"
  formats:
    - html
    - json
engines:
  - name: google
    engine: google
    shortcut: g
  - name: bing
    engine: bing
    shortcut: b
  - name: duckduckgo
    engine: duckduckgo
    shortcut: ddg
  - name: wikipedia
    engine: wikipedia
    shortcut: wp
  - name: github
    engine: github
    shortcut: gh
```

### 4. Provider Health Check

On startup and periodically, check which providers are available:

```python
class SearchProviderRegistry:
    async def check_providers(self) -> dict[str, bool]:
        available = {}

        # SearXNG
        try:
            async with httpx.AsyncClient(timeout=2) as c:
                r = await c.get("http://localhost:8888/healthz")
                available["searxng"] = r.status_code == 200
        except:
            available["searxng"] = False

        # DuckDuckGo — always available (Python library)
        available["duckduckgo"] = True

        # Perplexity — check for API key
        available["perplexity"] = bool(await get_setting("llm.api_key.perplexity"))

        # Tavily — check for API key
        available["tavily"] = bool(await get_setting("search.api_key.tavily"))

        return available
```

### 5. Settings UI

Add to the API Keys tab in Settings:

- **Perplexity API Key** (optional, for deep search)
- **Tavily API Key** (optional, alternative search)
- **SearXNG URL** (default: `http://localhost:8888`, configurable)

### 6. Tool Definitions

```python
WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web for information. Returns titles, URLs, and snippets. Use web_read to get full page content.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query"
                },
                "num_results": {
                    "type": "integer",
                    "description": "Number of results (default 10, max 20)",
                    "default": 10
                }
            },
            "required": ["query"]
        }
    }
}

WEB_READ_TOOL = {
    "type": "function",
    "function": {
        "name": "web_read",
        "description": "Fetch and read the content of a web page. Returns extracted text content. Use after web_search to read specific results.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "URL to read"
                },
                "max_length": {
                    "type": "integer",
                    "description": "Maximum content length in characters (default 5000)",
                    "default": 5000
                }
            },
            "required": ["url"]
        }
    }
}
```

## Dependencies

**Required (zero-config)**:
- `duckduckgo-search` — Python library, no API key
- `httpx` — HTTP client for web_read
- `trafilatura` — content extraction from HTML
- `beautifulsoup4` — fallback HTML parsing

**Optional**:
- SearXNG Docker container (recommended)
- `openai` — for Perplexity API (already installed)

## Stories

### Story W1: DuckDuckGo Search + web_read (zero-config)
- Implement `web_search` handler with DuckDuckGo provider
- Implement `web_read` handler with trafilatura extraction
- Add tool definitions to registry
- Update default agent tools to include `web_read`
- Add dependencies: `duckduckgo-search`, `trafilatura`, `httpx`
- Tests: search returns results, web_read extracts content

### Story W2: SearXNG Integration
- Add SearXNG to `docker-compose.dev.yml`
- Create `docker/searxng/settings.yml`
- Implement SearXNG search provider
- Auto-detect SearXNG availability
- Falls back to DuckDuckGo if SearXNG is down

### Story W3: Provider Registry + Settings
- Provider health check on startup
- SearXNG URL configurable in settings
- Perplexity/Tavily API key fields in Settings UI
- `GET /api/v1/settings/search/providers` — list available providers
- Provider preference configurable per agent

### Story W4: Perplexity Deep Search (Optional)
- Implement Perplexity provider
- Add `deep_search` parameter to web_search tool
- When `deep_search=true` and Perplexity available, use it for AI-synthesized answers with citations

## Rollout

**Phase 1 (Story W1)**: Ship immediately. DuckDuckGo works out of the box. `web_read` lets the agent actually read pages. This alone solves the user's problem.

**Phase 2 (Stories W2-W3)**: Add SearXNG for better quality and no rate limits. Provider selection and settings.

**Phase 3 (Story W4)**: Perplexity for when you want the best possible answer.

## Open Questions

1. **Rate limiting**: DuckDuckGo may throttle heavy use. SearXNG solves this but requires Docker. Should we add a simple delay between DDG searches? Recommendation: yes, 1 second minimum between DDG calls.

2. **Caching**: Should we cache search results? Recommendation: yes, 1 hour TTL in SQLite. Same query within an hour returns cached results. Saves API calls and speeds up repeated searches.

3. **Content extraction quality**: `trafilatura` vs `readability-lxml` vs `newspaper3k`? Recommendation: `trafilatura` — best overall quality for article extraction, handles most sites well, actively maintained.

4. **Sandbox considerations**: When running in Docker sandbox, web_search and web_read need network access. The `--network none` flag was already removed (commit `9d30c52`). Should these tools run on the host or inside the sandbox? Recommendation: host — the backend makes the HTTP calls, not the sandbox container.
