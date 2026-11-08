"""
Enterprise AI Hub - External APIs Manager & Smart Failover Engine
Connects dynamically to Oracle APEX table `AI_EXTERNAL_APIS` via ORDS REST API:
  GET {ORDS_BASE_URL}/external-apis
Categorizes keys by `api_type` ('FINANCE', 'SEARCH', 'SCRAPING', etc.)
and automatically rotates/switches to the next priority key if a key runs out of credit or hits rate limits.
Falls back seamlessly to local .env configuration if ORDS is unreachable.
"""

import os
import time
import logging
from typing import List, Dict, Any, Optional
import requests

logger = logging.getLogger("external_apis_manager")

# In-memory cache for external APIs from Oracle APEX table AI_EXTERNAL_APIS
_cached_external_apis: List[Dict[str, Any]] = []
_cached_external_apis_time: float = 0.0
_cooldown_keys: Dict[str, float] = {}  # key: f"{api_type}:{api_key}", val: expire_timestamp

def _get_ords_base_url() -> str:
    """Retrieves ORDS base URL from environment or server config."""
    ords_base = os.environ.get("ORDS_BASE_URL", "").strip()
    if not ords_base:
        try:
            from pathlib import Path
            server_dir = Path(__file__).resolve().parent.parent
            for env_cand in [server_dir / ".env", server_dir.parent / ".env"]:
                if env_cand.exists():
                    with open(env_cand, "r", encoding="utf-8") as f:
                        for line in f:
                            if line.startswith("ORDS_BASE_URL="):
                                ords_base = line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
                if ords_base:
                    break
        except Exception:
            pass
    if not ords_base or "<your-db" in ords_base:
        ords_base = "https://g086cd062cdff6a-services.adb.ap-singapore-1.oraclecloudapps.com/ords/aiagent/engine"
    return ords_base.rstrip("/")

def fetch_external_apis_from_ords(force: bool = False) -> List[Dict[str, Any]]:
    """
    Fetches active external APIs from Oracle APEX table AI_EXTERNAL_APIS.
    Caches results for 300 seconds (5 minutes) for high performance.
    """
    global _cached_external_apis, _cached_external_apis_time
    now = time.time()
    if not force and _cached_external_apis and (now - _cached_external_apis_time < 300):
        return _cached_external_apis

    base_url = _get_ords_base_url()
    # Try endpoints: /external-apis or /engine/external-apis
    candidates = [
        f"{base_url}/external-apis",
        f"{base_url.split('/engine')[0]}/engine/external-apis" if "/engine" in base_url else f"{base_url}/engine/external-apis"
    ]

    for url in candidates:
        try:
            res = requests.get(url, timeout=6)
            if res.ok:
                items = res.json().get("items", [])
                if items:
                    _cached_external_apis = items
                    _cached_external_apis_time = now
                    logger.info(f"Loaded {len(items)} external APIs from Oracle APEX ({url})")
                    return items
        except Exception as e:
            logger.debug(f"ORDS external-apis lookup notice for {url}: {e}")

    return _cached_external_apis

def get_active_keys_for_type(api_type: str) -> List[Dict[str, Any]]:
    """
    Returns active, non-cooldown keys for a given API_TYPE (e.g. 'FINANCE', 'SEARCH', 'SCRAPING')
    ordered by priority_order (1 = Primary, 2 = Secondary, etc.).
    Falls back to .env if Oracle APEX table is empty or offline.
    """
    target_type = api_type.strip().upper()
    now = time.time()

    # 1. Try Oracle APEX table AI_EXTERNAL_APIS
    all_apis = fetch_external_apis_from_ords()
    matched = []
    for item in all_apis:
        item_type = str(item.get("api_type") or "").strip().upper()
        is_active = str(item.get("is_active") or "Y").strip().upper()
        status = str(item.get("status") or "ACTIVE").strip().upper()

        if item_type == target_type and is_active == "Y" and status != "DISABLED":
            key_val = str(item.get("api_key") or "").strip()
            if not key_val:
                continue

            # Check if key is currently in temporary cooldown due to rate limit or quota
            cd_key = f"{target_type}:{key_val}"
            if cd_key in _cooldown_keys and now < _cooldown_keys[cd_key]:
                continue  # Skip temporarily exhausted key

            matched.append(item)

    # Sort by priority_order
    matched.sort(key=lambda x: int(x.get("priority_order") or 999))

    if matched:
        return matched

    # 2. Resilient Fallback to local .env
    fallback_key = None
    service_name = "Default Env Service"

    if target_type == "FINANCE":
        fallback_key = os.environ.get("EULERPOOL_API_KEY", "eu_prod_1794002761034_8gcodzj7yue").strip()
        service_name = "Eulerpool Fallback (.env)"
    elif target_type in ("SEARCH", "SCRAPING"):
        fallback_key = os.environ.get("FIRECRAWL_API_KEY", "fc-6bcc8f9252e54fc5861ee5ae421c42c7").strip()
        service_name = "Firecrawl Fallback (.env)"

    if fallback_key:
        return [{
            "api_id": 0,
            "service_name": service_name,
            "api_type": target_type,
            "api_key": fallback_key,
            "priority_order": 1,
            "is_active": "Y",
            "status": "ACTIVE"
        }]

    return []

def mark_key_exhausted(api_type: str, api_key: str, reason: str = "", cooldown_seconds: int = 1800):
    """
    Temporarily marks an API key as in cooldown/exhausted.
    The failover engine will immediately bypass this key and switch to the next priority key of the same type.
    """
    target_type = api_type.strip().upper()
    cd_key = f"{target_type}:{api_key.strip()}"
    _cooldown_keys[cd_key] = time.time() + cooldown_seconds
    print(f"[Key Failover Notice] Key for {target_type} ({api_key[:8]}...) marked EXHAUSTED/RATE_LIMITED for {cooldown_seconds}s. Reason: {reason}")
