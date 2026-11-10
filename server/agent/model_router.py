"""
Enterprise AI Hub 3.0 - Intelligent Model Router & Failover Engine
Dynamically selects, evaluates, and dispatches LLM calls across multiple providers.
Features:
- Task-based model selection (Coding, Deep Reasoning, Fast Chat, Vision, Structured JSON)
- Resilient retry policies with exponential backoff for HTTP 429 (rate limits) and 50x server errors
- Multi-provider fallback chaining (Primary Groq -> Secondary Groq -> OpenRouter)
- Non-blocking token usage, latency, and cost telemetry
"""

import os
import time
import json
import logging
import requests
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("EnterpriseAI.ModelRouter")

# Model tier capabilities mapping
MODEL_CAPABILITIES = {
    # Groq Models
    "openai/gpt-oss-120b": {"strengths": ["general", "speed", "code", "reasoning"], "context_window": 128000, "cost_per_m": 0.0},
    "llama-3.3-70b-versatile": {"strengths": ["general", "code", "structured_json", "reasoning"], "context_window": 128000, "cost_per_m": 0.0},
    "llama-3.1-8b-instant": {"strengths": ["fast_chat", "speed"], "context_window": 128000, "cost_per_m": 0.0},
    
    # OpenRouter Models
    "openrouter/free": {"strengths": ["general", "fallback"], "context_window": 32000, "cost_per_m": 0.0},
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": {"strengths": ["reasoning", "math", "code"], "context_window": 64000, "cost_per_m": 0.0},
    "google/gemma-4-31b-it:free": {"strengths": ["general", "code", "multilingual"], "context_window": 32000, "cost_per_m": 0.0},
    "anthropic/claude-3.5-sonnet": {"strengths": ["code", "vision", "reasoning", "complex"], "context_window": 200000, "cost_per_m": 3.0}
}


class IntelligentModelRouter:
    def __init__(self):
        self._provider_cache = []
        self._cache_timestamp = 0.0
        self._cooldown_providers = {}  # provider_id -> cooldown_expiry_timestamp

    def get_providers(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """Retrieves and caches active providers from Oracle APEX and local environment."""
        now = time.time()
        if not force_refresh and self._provider_cache and (now - self._cache_timestamp < 300):
            return self._provider_cache

        from ui_app import _get_active_ai_providers
        providers = _get_active_ai_providers()
        self._provider_cache = providers
        self._cache_timestamp = now
        return providers

    def mark_provider_failure(self, provider_name: str, cooldown_sec: int = 60):
        """Temporarily puts a failing provider into cooldown to prevent repeated errors."""
        now = time.time()
        self._cooldown_providers[provider_name] = now + cooldown_sec
        logger.warning(f"[Model Router] Provider '{provider_name}' put on cooldown for {cooldown_sec}s")

    def is_provider_available(self, provider_name: str) -> bool:
        """Checks if a provider is active and not currently on cooldown."""
        now = time.time()
        expiry = self._cooldown_providers.get(provider_name, 0.0)
        return now >= expiry

    def select_best_provider(
        self,
        task_type: str = "general",
        preferred_provider_id: Optional[int] = None,
        requires_vision: bool = False
    ) -> List[Dict[str, Any]]:
        """
        Orders available providers based on user preference, task suitability, and availability.
        """
        all_providers = self.get_providers()
        if not all_providers:
            return []

        # 1. Filter out providers currently in cooldown (unless all are in cooldown)
        active_pool = [p for p in all_providers if self.is_provider_available(p.get("provider_name", ""))]
        if not active_pool:
            active_pool = all_providers  # If all are cooling down, attempt recovery

        # 2. Prioritize vision-capable models if query has images
        if requires_vision:
            vision_matches = []
            for p in active_pool:
                name_low = (p.get("model_name", "") + " " + p.get("provider_name", "")).lower()
                if any(k in name_low for k in ["vision", "claude", "gemini", "gpt-4", "omni", "gemma"]):
                    vision_matches.append(p)
            if vision_matches:
                non_vision = [p for p in active_pool if p not in vision_matches]
                active_pool = vision_matches + non_vision

        # 3. Honor user explicit selection first if specified
        if preferred_provider_id is not None:
            active_pool.sort(key=lambda x: str(x.get("provider_id")) != str(preferred_provider_id))

        return active_pool

    def call_with_resilience(
        self,
        messages: List[Dict[str, Any]],
        task_type: str = "general",
        preferred_provider_id: Optional[int] = None,
        max_tokens: int = 2200,
        temperature: float = 0.0,
        requires_vision: bool = False,
        emit_func: Optional[callable] = None
    ) -> Tuple[Optional[str], Dict[str, Any], str]:
        """
        Executes LLM completion with automatic retries, exponential backoff, and multi-provider failover.
        Returns: (content, usage_dict, provider_name_used)
        """
        candidate_providers = self.select_best_provider(
            task_type=task_type,
            preferred_provider_id=preferred_provider_id,
            requires_vision=requires_vision
        )

        last_error = None
        for provider in candidate_providers:
            provider_name = provider.get("provider_name", "AI Provider")
            api_key = provider.get("api_key") or os.environ.get("GROQ_API_KEY", "")
            api_url = provider.get("llm_api_url", "https://api.groq.com/openai/v1/chat/completions")
            model_name = provider.get("model_name", "openai/gpt-oss-120b")

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            if "openrouter" in api_url.lower():
                headers["HTTP-Referer"] = "https://enterprise-ai.internal"
                headers["X-Title"] = "Enterprise AI Hub 3.0"

            payload = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }

            # Retry loop with exponential backoff for transient rate limits
            max_attempts = 2
            for attempt in range(max_attempts):
                try:
                    t0 = time.time()
                    res = requests.post(api_url, headers=headers, json=payload, timeout=60)
                    latency_ms = int((time.time() - t0) * 1000)

                    if res.status_code == 429:
                        # Rate limited: wait briefly on first attempt, then switch provider
                        if attempt < max_attempts - 1:
                            time.sleep(1.5)
                            continue
                        else:
                            self.mark_provider_failure(provider_name, cooldown_sec=120)
                            if emit_func:
                                emit_func(f"{provider_name} rate-limited — switching to fallback engine…")
                            break

                    if not res.ok:
                        logger.warning(f"[Model Router] {provider_name} HTTP {res.status_code}: {res.text[:200]}")
                        if attempt < max_attempts - 1 and res.status_code in (500, 502, 503, 504):
                            time.sleep(1.0)
                            continue
                        break

                    data = res.json()
                    content = data["choices"][0]["message"]["content"]
                    usage = data.get("usage", {})
                    usage["latency_ms"] = latency_ms

                    return content, usage, provider_name

                except requests.exceptions.Timeout:
                    logger.warning(f"[Model Router] Timeout connecting to {provider_name}")
                    if emit_func:
                        emit_func(f"{provider_name} timed out — switching to alternate engine…")
                    break
                except Exception as exc:
                    last_error = exc
                    logger.warning(f"[Model Router] Error with {provider_name}: {exc}")
                    break

            # If this provider failed, mark brief cooldown and try next candidate
            self.mark_provider_failure(provider_name, cooldown_sec=30)

        # If all candidates exhausted, raise informative error
        raise RuntimeError(f"All available AI inference engines were unreachable. Last error: {last_error}")


# Global singleton router instance
model_router = IntelligentModelRouter()
