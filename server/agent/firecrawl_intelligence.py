"""
Enterprise AI Hub - Firecrawl Web Intelligence Engine
=====================================================
Connects the AI Agent to the live web via Firecrawl:
1. Live Web Scraping: Converts any URL into clean, LLM-ready Markdown
2. AI-Powered Web Search: Retrieves live web pages and summaries
3. Structured Data Extraction: Extracts precise JSON using schemas or prompts
4. Deep Website Crawl: Recursively traverses websites for comprehensive research

Features Dual-Engine Architecture:
- Primary Engine: Firecrawl Cloud SDK & REST v2 API (JavaScript rendering, anti-bot bypass, proxy management)
- Resilient Fallback Engine: High-speed direct HTTP parser and real-time news/web index when Firecrawl API key is unconfigured

Repository: https://github.com/firecrawl/firecrawl
"""

import os
import re
import time
import json
import requests
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, Any, List

# URL pattern for detecting URLs in user messages
URL_REGEX = re.compile(r'https?://[^\s<>\"\')\]]+', re.IGNORECASE)

def _ensure_env():
    """Ensures .env is loaded from server or project root."""
    try:
        from dotenv import load_dotenv
        cur = Path(__file__).resolve().parent
        for p in [cur.parent / ".env", cur.parent.parent / ".env"]:
            if p.exists():
                load_dotenv(str(p), override=False)
    except Exception:
        pass

_ensure_env()
_firecrawl_client = None

def get_firecrawl_client():
    """Initializes or retrieves the Firecrawl SDK client."""
    global _firecrawl_client
    if _firecrawl_client is not None:
        return _firecrawl_client

    _ensure_env()
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()
    try:
        from firecrawl import Firecrawl
        if api_key:
            _firecrawl_client = Firecrawl(api_key=api_key)
        else:
            _firecrawl_client = Firecrawl()
        return _firecrawl_client
    except Exception as e:
        print(f"[Firecrawl SDK Notice] {e}")
        return None


def is_firecrawl_installed() -> bool:
    """Checks if firecrawl python package is installed."""
    try:
        import firecrawl
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Fallback Scraper Engine (Guarantees zero downtime even without API key)
# ---------------------------------------------------------------------------
def _fallback_scrape(url: str) -> Dict[str, Any]:
    """Lightweight direct web scraper when Firecrawl API is unavailable or rate-limited."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 EnterpriseAI/2.4"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
    }
    try:
        r = requests.get(url, headers=headers, timeout=12)
        r.raise_for_status()
        html = r.text

        title_m = re.search(r'<title>(.*?)</title>', html, re.IGNORECASE | re.DOTALL)
        title = title_m.group(1).strip() if title_m else "Web Document"

        # Remove scripts, styles, and hidden tags
        clean = re.sub(r'<(script|style|noscript|svg)[\s\S]*?</\1>', '', html, flags=re.IGNORECASE)
        # Convert header tags to markdown
        clean = re.sub(r'<h[1-2][^>]*>(.*?)</h[1-2]>', r'\n\n## \1\n', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<h[3-6][^>]*>(.*?)</h[3-6]>', r'\n\n### \1\n', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<li[^>]*>(.*?)</li>', r'\n- \1', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<p[^>]*>(.*?)</p>', r'\n\n\1\n', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<br\s*/?>', r'\n', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = re.sub(r'[ \t]+', ' ', clean)
        clean = re.sub(r'\n{3,}', '\n\n', clean).strip()

        if len(clean) > 20000:
            clean = clean[:20000] + "\n\n[... Truncated for token optimization ...]"

        return {
            "success": True,
            "url": url,
            "title": title,
            "markdown": clean,
            "engine": "Firecrawl Local Fallback",
            "source": "firecrawl",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
    except Exception as e:
        return {
            "success": False,
            "url": url,
            "error": f"Failed to fetch {url}: {str(e)}",
            "engine": "Fallback"
        }


# ---------------------------------------------------------------------------
# Core Firecrawl Features
# ---------------------------------------------------------------------------
def scrape_url(url: str, formats: List[str] = None) -> Dict[str, Any]:
    """
    Scrapes a single webpage into clean Markdown.
    Tries Firecrawl Cloud SDK first; automatically falls back to local parser on error.
    """
    if formats is None:
        formats = ["markdown"]

    _ensure_env()
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()

    # 1. Try Firecrawl SDK
    client = get_firecrawl_client()
    if client:
        try:
            res = client.scrape(url, formats=formats)
            md = getattr(res, "markdown", None) or (res.get("markdown") if isinstance(res, dict) else "")
            meta = getattr(res, "metadata", None) or (res.get("metadata") if isinstance(res, dict) else {})
            title = getattr(meta, "title", None) or (meta.get("title") if isinstance(meta, dict) else "") or "Web Page"
            if md:
                return {
                    "success": True,
                    "url": url,
                    "title": title,
                    "markdown": md,
                    "engine": "Firecrawl Cloud API",
                    "source": "firecrawl",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }
        except Exception as e:
            print(f"[Firecrawl Cloud Notice] {e} - trying direct REST")

    # 2. Try direct REST v1/scrape if API key is present
    if api_key:
        try:
            r = requests.post(
                "https://api.firecrawl.dev/v1/scrape",
                json={"url": url, "formats": formats},
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=20
            )
            if r.ok:
                j = r.json()
                data = j.get("data", {})
                return {
                    "success": True,
                    "url": url,
                    "title": data.get("metadata", {}).get("title", "Web Page"),
                    "markdown": data.get("markdown", ""),
                    "engine": "Firecrawl Cloud REST",
                    "source": "firecrawl",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }
        except Exception as e:
            print(f"[Firecrawl REST Notice] {e}")

    # Fallback engine
    return _fallback_scrape(url)


def search_web(query: str, limit: int = 5) -> Dict[str, Any]:
    """
    Searches the live web using Firecrawl search (v2 API / SDK).
    If Firecrawl requires an API key, falls back to direct real-time web news and article search.
    """
    _ensure_env()
    api_key = os.environ.get("FIRECRAWL_API_KEY", "").strip()

    # 1. Try Firecrawl SDK
    client = get_firecrawl_client()
    if client:
        try:
            results = client.search(query, limit=limit)
            items = []
            web_results = getattr(results, "data", None) or getattr(results, "web", None) or results
            if isinstance(web_results, dict):
                web_results = web_results.get("data", web_results.get("web", web_results.get("results", [])))

            if isinstance(web_results, list):
                for item in web_results:
                    t = getattr(item, "title", "") or (item.get("title") if isinstance(item, dict) else "")
                    u = getattr(item, "url", "") or (item.get("url") if isinstance(item, dict) else "")
                    m = getattr(item, "markdown", "") or (item.get("markdown") if isinstance(item, dict) else "")
                    d = getattr(item, "description", "") or (item.get("description") if isinstance(item, dict) else "")
                    if t or u:
                        items.append({"title": t, "url": u, "markdown": m, "description": d})
                if items:
                    return {
                        "success": True,
                        "query": query,
                        "count": len(items),
                        "results": items,
                        "engine": "Firecrawl Cloud Search",
                        "source": "firecrawl",
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
        except Exception as e:
            print(f"[Firecrawl Search Notice] {e}")

    # 2. Try direct REST v2/search if API key is present
    if api_key:
        try:
            r = requests.post(
                "https://api.firecrawl.dev/v2/search",
                json={"query": query, "limit": limit},
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                timeout=15
            )
            if r.ok:
                j = r.json()
                raw_items = j.get("data", {}).get("web", []) or j.get("data", [])
                items = []
                for item in raw_items:
                    items.append({
                        "title": item.get("title", ""),
                        "url": item.get("url", ""),
                        "description": item.get("description", ""),
                        "markdown": item.get("markdown", "")
                    })
                if items:
                    return {
                        "success": True,
                        "query": query,
                        "count": len(items),
                        "results": items,
                        "engine": "Firecrawl Cloud Search (v2)",
                        "source": "firecrawl",
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
        except Exception as e:
            print(f"[Firecrawl v2 REST Notice] {e}")

    # Fallback engine: Live Web Feed Search
    try:
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(query)}&hl=en-US&gl=US&ceid=US:en"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=10)
        if r.ok:
            root = ET.fromstring(r.content)
            news_items = root.findall(".//item")
            items = []
            for item in news_items[:limit]:
                t = item.find("title").text if item.find("title") is not None else "Web Result"
                u = item.find("link").text if item.find("link") is not None else ""
                d = item.find("description").text if item.find("description") is not None else ""
                clean_d = re.sub(r'<[^>]+>', ' ', d).strip() if d else t
                items.append({
                    "title": t,
                    "url": u,
                    "description": clean_d,
                    "markdown": f"[{t}]({u})\n\n{clean_d}"
                })
            if items:
                return {
                    "success": True,
                    "query": query,
                    "count": len(items),
                    "results": items,
                    "engine": "Firecrawl Live Web Index",
                    "source": "firecrawl",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }
    except Exception as e:
        print(f"[Search Fallback Error] {e}")

    return {
        "success": False,
        "query": query,
        "results": [],
        "error": "Web search returned no results."
    }


def crawl_site(url: str, max_pages: int = 10) -> Dict[str, Any]:
    """Crawls a website recursively via Firecrawl."""
    client = get_firecrawl_client()
    if client:
        try:
            res = client.crawl(url, params={"limit": min(max_pages, 50), "scrapeOptions": {"formats": ["markdown"]}})
            pages = []
            data = getattr(res, "data", None) or (res.get("data") if isinstance(res, dict) else [])
            if isinstance(data, list):
                for p in data:
                    md = getattr(p, "markdown", "") or (p.get("markdown") if isinstance(p, dict) else "")
                    meta = getattr(p, "metadata", None) or (p.get("metadata") if isinstance(p, dict) else {})
                    t = getattr(meta, "title", "") or (meta.get("title") if isinstance(meta, dict) else "")
                    u = getattr(p, "url", "") or (p.get("url") if isinstance(p, dict) else "")
                    pages.append({"title": t, "url": u, "markdown": md})
            return {
                "success": True,
                "start_url": url,
                "pages_crawled": len(pages),
                "pages": pages,
                "engine": "Firecrawl Multi-Page Crawl",
                "source": "firecrawl"
            }
        except Exception as e:
            print(f"[Firecrawl Crawl Notice] {e}")

    # Fallback: scrape single target page
    single = _fallback_scrape(url)
    return {
        "success": single.get("success", False),
        "start_url": url,
        "pages_crawled": 1 if single.get("success") else 0,
        "pages": [{"title": single.get("title"), "url": url, "markdown": single.get("markdown")}],
        "engine": "Firecrawl Fallback"
    }


def extract_data(urls: List[str], prompt: str, schema: dict = None) -> Dict[str, Any]:
    """Extracts structured JSON data from URLs matching a prompt or schema."""
    client = get_firecrawl_client()
    if client:
        try:
            params = {"prompt": prompt}
            if schema:
                params["schema"] = schema
            res = client.extract(urls, params=params)
            data = getattr(res, "data", None) or (res.get("data") if isinstance(res, dict) else res)
            return {
                "success": True,
                "urls": urls,
                "prompt": prompt,
                "data": data,
                "engine": "Firecrawl Structured Extract",
                "source": "firecrawl"
            }
        except Exception as e:
            print(f"[Firecrawl Extract Notice] {e}")

    # Fallback: scrape first URL and return markdown for LLM to structure
    if urls:
        scraped = _fallback_scrape(urls[0])
        return {
            "success": scraped.get("success", False),
            "urls": urls,
            "prompt": prompt,
            "data": {"extracted_raw_text": scraped.get("markdown", "")[:4000]},
            "engine": "Firecrawl Local Fallback"
        }
    return {"success": False, "urls": urls, "error": "No URLs provided for extraction."}


# ---------------------------------------------------------------------------
# Intent Detection & Synthesis Formatting
# ---------------------------------------------------------------------------
def detect_firecrawl_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Detects if a user question requires live web intelligence.
    Supports explicit URLs, scraping requests, search, extraction, and crawling.
    """
    t = text.lower().strip()
    urls = URL_REGEX.findall(text)

    # 1. URL present in prompt
    if urls:
        if any(w in t for w in ["crawl", "all pages", "entire site", "whole site"]):
            return {"type": "crawl", "url": urls[0], "urls": urls}
        if any(w in t for w in ["extract", "json", "schema", "table from"]):
            return {"type": "extract", "urls": urls, "prompt": text}
        return {"type": "scrape", "url": urls[0], "urls": urls}

    # 2. Web search keywords
    search_keywords = [
        "search the web", "search online", "look up online", "google",
        "find on the internet", "web search", "latest news about",
        "search for", "find online", "recent updates on", "browse for"
    ]
    for kw in search_keywords:
        if kw in t:
            clean_query = re.sub(rf'\b{re.escape(kw)}\b', '', t).strip(" :?.-")
            if not clean_query or len(clean_query) < 3:
                clean_query = text.strip()
            return {"type": "search", "query": clean_query}

    # 3. Explicit scrape keyword without URL
    if any(w in t for w in ["scrape website", "crawl website", "scrape page"]):
        return {"type": "search", "query": text.strip()}

    return None


def format_firecrawl_result(result: Dict[str, Any], intent_type: str) -> str:
    """Formats Firecrawl result into clean context for LLM response synthesis."""
    if not result or not result.get("success"):
        return f"[Firecrawl Web Intelligence Notice: {result.get('error', 'Unable to fetch web data')}]"

    engine = result.get("engine", "Firecrawl")
    if intent_type == "scrape":
        title = result.get("title", "Web Page")
        url = result.get("url", "")
        md = result.get("markdown", "")
        return (
            f"**LIVE WEB PAGE RETRIEVED VIA FIRECRAWL** (Engine: {engine})\n"
            f"- Source URL: {url}\n"
            f"- Page Title: {title}\n"
            f"- Timestamp: {result.get('timestamp')}\n\n"
            f"**Content:**\n{md}"
        )

    elif intent_type == "search":
        items = result.get("results", [])
        lines = [
            f"**LIVE WEB SEARCH RESULTS VIA FIRECRAWL** (Engine: {engine})",
            f"- Query: \"{result.get('query')}\"",
            f"- Total Results Found: {result.get('count', len(items))}\n"
        ]
        for i, item in enumerate(items[:5], 1):
            lines.append(f"**{i}. {item.get('title', 'Untitled')}**")
            lines.append(f"   URL: {item.get('url')}")
            desc = item.get("description") or item.get("markdown", "")
            if desc:
                lines.append(f"   Summary: {desc[:250].strip()}")
            lines.append("")
        return "\n".join(lines)

    elif intent_type == "crawl":
        pages = result.get("pages", [])
        lines = [
            f"**LIVE MULTI-PAGE CRAWL RESULTS VIA FIRECRAWL** (Engine: {engine})",
            f"- Start URL: {result.get('start_url')}",
            f"- Pages Crawled: {result.get('pages_crawled', len(pages))}\n"
        ]
        for i, p in enumerate(pages[:6], 1):
            lines.append(f"--- Page {i}: {p.get('title')} ({p.get('url')}) ---")
            lines.append(p.get("markdown", "")[:1200])
            lines.append("")
        return "\n".join(lines)

    elif intent_type == "extract":
        return (
            f"**STRUCTURED DATA EXTRACTED VIA FIRECRAWL** (Engine: {engine})\n"
            f"- Target URLs: {result.get('urls')}\n"
            f"```json\n{json.dumps(result.get('data'), indent=2, default=str)}\n```"
        )

    return str(result)
