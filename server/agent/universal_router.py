"""
Enterprise AI Hub 3.0 - Universal Intent & Capability Router
Classifies incoming user inquiries into specialized processing channels:
1. PURE_MATH: Instant arithmetic evaluation (0 tokens, <1ms)
2. FILE_EXPORT_CLARIFICATION: Ambiguous file download requests requiring dataset selection
3. ENTERPRISE_DATA: Database querying, SQL aggregations, pivot analysis, or cross-table joins
4. WEB_RESEARCH: Real-time search, URL browsing, live weather, stock, or commodity intelligence
5. UNIVERSAL_CONVERSATIONAL: General knowledge, coding, mathematics, education, creative writing, and reasoning
"""

import re
from typing import Dict, Any, List, Optional
from enum import Enum


class QueryRoute(str, Enum):
    PURE_MATH = "pure_math"
    FILE_EXPORT_CLARIFICATION = "file_export_clarification"
    ENTERPRISE_DATA = "enterprise_data"
    WEB_RESEARCH = "web_research"
    UNIVERSAL_CONVERSATIONAL = "universal_conversational"


UNIVERSAL_ASSISTANT_SYSTEM_PROMPT = """You are Enterprise AI Hub 3.0, a world-class, general-purpose conversational AI assistant.
You possess profound expertise across computer science, software engineering, mathematics, natural sciences, data analysis, professional writing, business strategy, and education.

CORE OPERATIONAL PRINCIPLES:
1. DIRECT & ENGAGING COMMUNICATION:
   - Begin your answers directly and authoritatively without robotic filler phrases (e.g. avoid "Certainly! I'd be happy to help with that").
   - Match the user's language, tone, and depth. Support English, Urdu script, and Roman Urdu naturally.
   - For simple queries, be concise. For complex architectural, educational, or technical queries, provide thorough, well-structured, multi-step explanations.

2. CODE & TECHNICAL ACCURACY:
   - Always format code blocks with the correct language tag (e.g. ```python, ```javascript, ```sql, ```html, ```css, ```json, ```bash).
   - Write clean, production-grade, well-commented, and robust code. Include error handling and follow best practices.
   - Explain how the code works, any dependencies required, and common edge cases.

3. MATHEMATICAL & LOGICAL REASONING:
   - For multi-step math or logical problems, break down the derivation step by step.
   - Use clear formatting for equations, matrices, and calculations.

4. ACADEMIC & EDUCATIONAL TUTORING:
   - When explaining complex scientific, technical, or philosophical concepts, use clear analogies, intuition-building explanations, and structured breakdowns.
   - Offer relevant follow-up avenues or deeper explorations where helpful.

5. PROFESSIONAL WRITING & ANALYSIS:
   - Produce polished, executive-grade documents, essays, emails, proposals, and summaries with strong narrative structure.
   - Use Markdown formatting (headings, bullet points, clean tables) to make complex information effortlessly scannable.

6. CITATIONS & FACTUAL INTEGRITY:
   - When provided with real-time web research or external evidence, cite sources cleanly and accurately.
   - Clearly distinguish between established facts, working estimates, and theoretical perspectives. Acknowledge uncertainty honestly where appropriate.
"""


def classify_query_route(
    question: str,
    endpoints: Optional[List[Dict[str, Any]]] = None,
    history: Optional[List[Dict[str, Any]]] = None,
    general_ai_enabled: bool = True,
    has_images: bool = False,
    has_docs: bool = False
) -> Dict[str, Any]:
    """
    Deterministically evaluates the user question to determine the optimal processing route.
    Guarantees that enterprise database questions always use the deterministic DuckDB engine,
    while universal, educational, and coding questions route to the Universal Conversational Core.
    """
    q = question.strip()
    q_lower = q.lower()
    endpoints = endpoints or []

    # 1. Direct Arithmetic Fast-Path (0 Tokens)
    from ui_app import is_pure_math_expression
    if is_pure_math_expression(q):
        return {
            "route": QueryRoute.PURE_MATH,
            "reason": "Pure arithmetic expression detected for deterministic evaluation"
        }

    # 2. Strict File Export Clarification Guard
    file_export_pattern = r"\b(pdf|excel|xlsx?|word|docx?|ppt|powerpoint|presentation|spreadsheet)\b"
    has_export_kw = bool(re.search(file_export_pattern, q_lower))
    
    from ui_app import _match_endpoint, _match_endpoints_multi
    matched_ep = _match_endpoint(q, endpoints)
    matched_multi = _match_endpoints_multi(q, endpoints)

    if has_export_kw and not matched_ep and not matched_multi and not has_images and not has_docs:
        # Check if the user is asking to export something specific or if it's an ambiguous table export
        export_action = any(k in q_lower for k in ["give me the data", "export this", "export to", "download as", "generate", "as a pdf", "in excel"])
        if export_action or len(q.split()) <= 8:
            return {
                "route": QueryRoute.FILE_EXPORT_CLARIFICATION,
                "reason": "Ambiguous file export command without explicit database table reference"
            }

    # 3. Enterprise Database Entity & Schema Match
    # Detects explicit table references, column synonyms, or multi-table join intents
    is_database_intent = bool(matched_ep or matched_multi)
    
    db_keywords = [
        "employee", "employees", "salary", "salaries", "payroll", "headcount", "staff",
        "department", "departments", "designation", "hire date", "job title",
        "product", "products", "inventory", "stock level", "in stock", "out of stock", "unit price",
        "lead", "leads", "google map", "rating", "reviews", "phone number", "address",
        "table", "database", "record", "records", "rows", "columns", "dataset", "cross table", "join",
        "earner", "earners", "earning", "earnings", "top earner", "top earners",
        "highest earner", "highest earners", "highest paid", "top paid", "lowest paid",
        "highest salary", "lowest salary", "breakdown", "compensation", "wage", "wages", "bonus"
    ]
    has_db_entity = any(re.search(r"\b" + re.escape(w) + r"\b", q_lower) for w in db_keywords)

    if is_database_intent or (has_db_entity and endpoints):
        return {
            "route": QueryRoute.ENTERPRISE_DATA,
            "target_endpoint": matched_ep,
            "multi_endpoints": matched_multi,
            "reason": "Enterprise database table, column, or entity reference detected"
        }

    # 4. Whitelisted Business Feeds (Forex, Fuel, PSX) & Web Research
    from agent.external_intelligence import (
        extract_forex_intent,
        extract_stock_intent,
        extract_fuel_intent,
        is_psx_stock
    )

    forex_intent = extract_forex_intent(q)
    fuel_intent = extract_fuel_intent(q)
    stock_intent = extract_stock_intent(q, history or [])

    # If General AI is OFF, filter stock_intent strictly to PSX (Pakistan Stock Exchange)
    if not general_ai_enabled and stock_intent:
        if not is_psx_stock(stock_intent):
            stock_intent = None

    has_whitelisted_feed = bool(forex_intent or fuel_intent or stock_intent)

    if has_whitelisted_feed:
        return {
            "route": QueryRoute.WEB_RESEARCH,
            "forex_intent": forex_intent,
            "stock_intent": stock_intent,
            "fuel_intent": fuel_intent,
            "weather_intent": None,
            "has_url": False,
            "reason": "Whitelisted enterprise business feed detected (Forex, Fuel, or PSX)"
        }

    # Open-world web research, live weather, external URLs (strictly gated by General AI toggle)
    if general_ai_enabled:
        from agent.weather_helper import extract_weather_intent

        weather_intent = extract_weather_intent(q)
        if not weather_intent and any(k in q_lower for k in ["weather", "temperature", "forecast", "climate", "mausam", "rain"]):
            weather_intent = "auto"
        has_url = bool(re.search(r"https?://\S+", q))

        has_web_kw = any(k in q_lower for k in [
            "search online", "search web", "browse", "latest news", "current price",
            "weather", "forecast", "dollar rate", "stock price"
        ])

        if weather_intent or has_url or has_web_kw:
            return {
                "route": QueryRoute.WEB_RESEARCH,
                "forex_intent": None,
                "stock_intent": None,
                "fuel_intent": None,
                "weather_intent": weather_intent,
                "has_url": has_url,
                "reason": "Real-time web research, market feed, or external intelligence detected"
            }

    # 5. Strict Enterprise Mode Guard (when General AI is switched OFF)
    # When General AI is OFF and no external documents/images are being queried,
    # the query must route to the enterprise database engine so that it either queries
    # the schema or triggers the Security Policy Guard rejection.
    if not general_ai_enabled and not has_docs and not has_images:
        return {
            "route": QueryRoute.ENTERPRISE_DATA,
            "target_endpoint": matched_ep,
            "multi_endpoints": matched_multi,
            "reason": "Strict internal database mode: General AI is switched off"
        }

    # 6. Default: Universal Conversational Core
    # General knowledge, coding, mathematics, education, creative writing, reasoning
    return {
        "route": QueryRoute.UNIVERSAL_CONVERSATIONAL,
        "reason": "General knowledge, coding, or conversational query"
    }
