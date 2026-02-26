"""Web search and web_read tools — DuckDuckGo + trafilatura."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger("bond.agent.tools.web")

# Rate-limiting for DuckDuckGo: 1 second minimum between calls
_ddg_lock = asyncio.Lock()
_ddg_last_call: float = 0.0


async def handle_web_search(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Search the web using DuckDuckGo."""
    query = arguments.get("query", "").strip()
    if not query:
        return {"error": "query is required"}

    num_results = min(arguments.get("num_results", 10), 20)

    global _ddg_last_call
    async with _ddg_lock:
        elapsed = time.monotonic() - _ddg_last_call
        if elapsed < 1.0:
            await asyncio.sleep(1.0 - elapsed)
        _ddg_last_call = time.monotonic()

    try:
        from duckduckgo_search import DDGS

        raw = await asyncio.to_thread(
            lambda: DDGS().text(query, max_results=num_results)
        )
        results = [
            {
                "title": r.get("title", ""),
                "url": r.get("href", ""),
                "snippet": r.get("body", ""),
            }
            for r in (raw or [])
        ]
        return {
            "results": results,
            "provider": "duckduckgo",
            "query": query,
        }
    except Exception as e:
        logger.warning("DuckDuckGo search failed: %s", e)
        return {"error": f"Web search failed: {e}", "query": query}


async def handle_web_read(
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Fetch and extract content from a web page."""
    url = arguments.get("url", "").strip()
    if not url:
        return {"error": "url is required"}

    max_length = arguments.get("max_length", 5000)

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=15.0,
        ) as client:
            resp = await client.get(url, headers={"User-Agent": "Bond/1.0"})
            resp.raise_for_status()

        html = resp.text

        # Try trafilatura first
        from trafilatura import extract

        content = await asyncio.to_thread(
            extract, html, include_links=True, include_tables=True
        )

        # Fallback to BeautifulSoup
        if not content:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            content = soup.get_text(separator="\n", strip=True)

        # Extract title
        title = ""
        from bs4 import BeautifulSoup as BS

        title_soup = BS(html, "html.parser")
        if title_soup.title and title_soup.title.string:
            title = title_soup.title.string.strip()

        # Truncate
        if len(content) > max_length:
            content = content[:max_length] + "\n\n[Content truncated]"

        return {
            "url": url,
            "title": title,
            "content": content,
            "length": len(content),
        }
    except httpx.HTTPStatusError as e:
        return {"error": f"HTTP {e.response.status_code} for {url}"}
    except httpx.RequestError as e:
        return {"error": f"Request failed for {url}: {e}"}
    except Exception as e:
        logger.warning("web_read failed for %s: %s", url, e)
        return {"error": f"Failed to read {url}: {e}"}
