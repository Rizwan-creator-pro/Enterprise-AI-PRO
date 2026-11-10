"""
Enterprise AI Hub 3.0 - Long-Term Persistent Memory Subsystem
Manages persistent user-approved memory across sessions with:
- Strict user-level isolation (user_id partitioning)
- Memory categories: PREFERENCE, PROJECT, SUMMARY, ENTERPRISE
- Sensitive secret sanitization (redacts passwords, tokens, API keys)
- Relevance retrieval for context injection
- Explicit user deletion and expiration policies
"""

import os
import re
import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger("EnterpriseAI.MemoryEngine")

# Memory storage path (JSON Lines per user or database synced)
MEMORY_STORE_DIR = Path(__file__).resolve().parent.parent / "data" / "user_memory"
MEMORY_STORE_DIR.mkdir(parents=True, exist_ok=True)

SENSITIVE_SECRET_PATTERNS = [
    r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|bearer|token)\s*(?:[:=]|\bis\b)\s*['\"]?([^\s'\"]+)",
    r"\b[A-Za-z0-9_-]{32,}\b"  # High entropy keys
]


def sanitize_memory_text(text: str) -> str:
    """Strips credentials, passwords, and sensitive API keys before saving to persistent memory."""
    sanitized = text
    for pat in SENSITIVE_SECRET_PATTERNS:
        sanitized = re.sub(pat, "[REDACTED_SECRET]", sanitized)
    return sanitized


class UserMemoryStore:
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.file_path = MEMORY_STORE_DIR / f"user_{user_id}_memory.json"
        self._memories: List[Dict[str, Any]] = self._load()

    def _load(self) -> List[Dict[str, Any]]:
        if not self.file_path.exists():
            return []
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Error loading memory for user {self.user_id}: {e}")
            return []

    def _save(self):
        try:
            with open(self.file_path, "w", encoding="utf-8") as f:
                json.dump(self._memories, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error persisting memory for user {self.user_id}: {e}")

    def add_memory(
        self,
        content: str,
        category: str = "PREFERENCE",
        project_tag: Optional[str] = None,
        ttl_days: Optional[int] = None
    ) -> Dict[str, Any]:
        """Adds a sanitized memory entry for the user."""
        clean_content = sanitize_memory_text(content.strip())
        if not clean_content:
            return {}

        now = time.time()
        expiry = now + (ttl_days * 86400) if ttl_days else None

        entry = {
            "id": f"mem_{int(now * 1000)}_{len(self._memories)}",
            "content": clean_content,
            "category": category.upper(),
            "project_tag": project_tag,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)),
            "expires_at": expiry
        }
        self._memories.append(entry)
        self._save()
        return entry

    def get_active_memories(self, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieves non-expired memories for this user."""
        now = time.time()
        active = []
        for m in self._memories:
            if m.get("expires_at") and now > m["expires_at"]:
                continue
            if category and m.get("category") != category.upper():
                continue
            active.append(m)
        return active

    def delete_memory(self, memory_id: str) -> bool:
        """Deletes a specific memory entry."""
        initial_len = len(self._memories)
        self._memories = [m for m in self._memories if m.get("id") != memory_id]
        if len(self._memories) < initial_len:
            self._save()
            return True
        return False

    def clear_all(self):
        """Clears all memories for this user."""
        self._memories = []
        self._save()

    def retrieve_relevant_context(self, query: str, max_items: int = 4) -> str:
        """
        Retrieves top relevant memories for prompt context injection using keyword scoring.
        """
        active = self.get_active_memories()
        if not active:
            return ""

        words = set(re.findall(r"\w{3,}", query.lower()))
        scored = []
        for m in active:
            c_low = m["content"].lower()
            score = sum(1 for w in words if w in c_low)
            # Boost user preferences
            if m.get("category") == "PREFERENCE":
                score += 1.5
            scored.append((score, m))

        scored.sort(key=lambda x: x[0], reverse=True)
        relevant = [m["content"] for score, m in scored[:max_items] if score > 0]
        if not relevant:
            # If no direct keyword match, return top preferences
            relevant = [m["content"] for m in active if m.get("category") == "PREFERENCE"][:2]

        if not relevant:
            return ""

        lines = [f"- {item}" for item in relevant]
        return "### USER PERSISTENT CONTEXT & PREFERENCES:\n" + "\n".join(lines)


def get_user_memory(user_id: int) -> UserMemoryStore:
    return UserMemoryStore(user_id)
