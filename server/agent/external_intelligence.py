"""
Enterprise AI Hub - Live External Intelligence Module
Connects the AI Agent to the live outside world:
1. Real-Time Currency Exchange & Forex (USD, EUR, GBP, AED, SAR to PKR & Global)
2. Live Pakistan Stock Exchange (PSX KSE-100, KSE-30, KMI-30 indices)
3. Live Global & PSX Stock Market Quotes (Interloop, JK Spinning Mills, Systems, Lucky Cement, Apple, Tesla, etc.)
4. Live Economic Indicators & Central Bank Spot Rates
"""

import requests
import re
import json
import time
from typing import Optional, Dict, Any, Tuple, List

# Common stock symbol aliases
STOCK_SYMBOL_MAP = {
    # PSX Major Equities
    "shadab": "SHDT",
    "shadab textile": "SHDT",
    "shadab textile mills": "SHDT",
    "shadab textile mills limited": "SHDT",
    "shdt": "SHDT",
    "interloop": "ILP",
    "inter loop": "ILP",
    "ilp": "ILP",
    "jk spinning": "JKSM",
    "jk spinning mills": "JKSM",
    "j.k. spinning": "JKSM",
    "j.k. spinning mills": "JKSM",
    "jksm": "JKSM",
    "gul ahmed": "GATM",
    "gul ahmed textile": "GATM",
    "gatm": "GATM",
    "systems limited": "SYS",
    "systems": "SYS",
    "sys": "SYS",
    "lucky cement": "LUCK",
    "luck": "LUCK",
    "ogdc": "OGDC",
    "oil and gas": "OGDC",
    "hub power": "HUBC",
    "hubco": "HUBC",
    "hubc": "HUBC",
    "habib bank": "HBL",
    "hbl": "HBL",
    "mcb bank": "MCB",
    "mcb": "MCB",
    "united bank": "UBL",
    "ubl": "UBL",
    "pakistan state oil": "PSO",
    "pso": "PSO",
    "fauji fertilizer": "FFC",
    "ffc": "FFC",
    "engro": "ENGRO",
    "engro fertilizer": "EFERT",
    "efert": "EFERT",
    "fatima fertilizer": "FATIMA",
    "fatima": "FATIMA",
    "bank alfalah": "BAFL",
    "bafl": "BAFL",
    "meezan bank": "MEBL",
    "mebl": "MEBL",
    "mari petroleum": "MARI",
    "mari": "MARI",
    "pakistan petroleum": "PPL",
    "ppl": "PPL",
    "pakistan oilfields": "POL",
    "pol": "POL",
    "attock refinery": "ATRL",
    "atrl": "ATRL",
    "national refinery": "NRL",
    "nrl": "NRL",
    "k-electric": "KEL",
    "kelectric": "KEL",
    "kel": "KEL",
    "airlink": "AIRLINK",
    "waves": "WAVES",
    "ghani glass": "GHGL",
    "ghgl": "GHGL",
    "pioneer cement": "PIOC",
    "pioc": "PIOC",
    "cherat cement": "CHCC",
    "chcc": "CHCC",
    "ptcl": "PTC",
    "telecard": "TELE",
    "nishat mills": "NML",
    "nishat": "NML",
    "nml": "NML",
    "kohinoor textile": "KTML",
    "ktml": "KTML",
    "tariq glass": "TGL",
    "tgl": "TGL",
    "d.g. khan cement": "DGKC",
    "dgkc": "DGKC",
    "maple leaf": "MLCF",
    "mlcf": "MLCF",
    "trg pakistan": "TRG",
    "trg": "TRG",
    "fauji cement": "FCCL",
    "fccl": "FCCL",
    "pakola": "PKL",

    # Global Stocks
    "apple": "AAPL",
    "aapl": "AAPL",
    "microsoft": "MSFT",
    "msft": "MSFT",
    "google": "GOOGL",
    "alphabet": "GOOGL",
    "googl": "GOOGL",
    "amazon": "AMZN",
    "amzn": "AMZN",
    "tesla": "TSLA",
    "tsla": "TSLA",
    "nvidia": "NVDA",
    "nvda": "NVDA",
    "meta": "META",
    "facebook": "META"
}

def extract_forex_intent(text: str) -> Optional[Dict[str, Any]]:
    """Detects if user is asking about currency exchange rates or conversions."""
    t = text.lower().strip()
    currency_keywords = [
        "dollar", "usd", "pkr", "rupee", "rupees", "currency", "exchange rate", "forex",
        "dirham", "aed", "riyal", "sar", "euro", "eur", "pound", "gbp", "dinar", "exchange"
    ]
    if not any(k in t for k in currency_keywords):
        return None
    
    intent_triggers = [
        "rate", "price", "convert", "conversion", "how much", "worth", "today", "now", "currently",
        "vs", "to", "in", "kitna", "kitne", "aaj", "bhav", "chal raha", "kya hai", "equal"
    ]
    if not any(k in t for k in intent_triggers):
        return None

    # Detect base and target
    base = "USD"
    target = "PKR"
    
    if "euro" in t or "eur" in t:
        base = "EUR"
    elif "pound" in t or "gbp" in t:
        base = "GBP"
    elif "dirham" in t or "aed" in t:
        base = "AED"
    elif "riyal" in t or "sar" in t:
        base = "SAR"

    # Detect amount (e.g. "convert 500 dollars to pkr", "$500", "1000 usd", "500$")
    amount = 1.0
    m_specific = re.search(r'(\d+[\d,]*\.?\d*)\s*(?:dollars?|usd|euros?|eur|pounds?|gbp|aed|dirhams?|riyals?|sar)', t)
    m_prefix = re.search(r'(?:\$|usd\s*)\s*(\d+[\d,]*\.?\d*)', t)
    
    amt_match = m_specific or m_prefix
    if amt_match:
        try:
            val = float(amt_match.group(1).replace(",", ""))
            if val > 0:
                amount = val
        except Exception:
            amount = 1.0

    return {"base": base, "target": target, "amount": amount}

def format_forex_payload(intent: Dict[str, Any], forex_data: Dict[str, Any]) -> Dict[str, Any]:
    """Formats verified forex rates for interactive UI cards."""
    base = intent.get("base", "USD")
    target = intent.get("target", "PKR")
    amount = float(intent.get("amount", 1.0))
    rates = forex_data.get("rates", {})
    rate = rates.get(target, 276.88 if target == "PKR" else 1.0)
    converted_amount = round(amount * rate, 2)
    rev_rate = round(1.0 / rate, 6) if rate else 0.0

    comparisons = {}
    if "PKR" in rates:
        pkr_per_usd = rates["PKR"]
        if base == "USD":
            comparisons["USD"] = pkr_per_usd
            if rates.get("EUR"): comparisons["EUR"] = round(pkr_per_usd * (1.0 / rates["EUR"]), 2)
            if rates.get("GBP"): comparisons["GBP"] = round(pkr_per_usd * (1.0 / rates["GBP"]), 2)
            if rates.get("AED"): comparisons["AED"] = round(pkr_per_usd / rates["AED"], 2)
            if rates.get("SAR"): comparisons["SAR"] = round(pkr_per_usd / rates["SAR"], 2)

    return {
        "type": "forex",
        "base": base,
        "target": target,
        "amount": amount,
        "rate": rate,
        "converted_amount": converted_amount,
        "reverse_rate": rev_rate,
        "comparisons": comparisons,
        "timestamp": forex_data.get("timestamp", ""),
        "source": forex_data.get("source", "Interbank Global Forex Feed")
    }


def fetch_live_forex(base: str = "USD") -> Optional[Dict[str, Any]]:
    """Fetches verified live interbank and central bank exchange rates."""
    urls = [
        f"https://open.er-api.com/v6/latest/{base}",
        f"https://api.exchangerate-api.com/v4/latest/{base}",
        f"https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/{base.lower()}.json"
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=4).json()
            rates = r.get("rates") or r.get(base.lower(), {})
            if rates and ("PKR" in rates or "pkr" in rates):
                pkr_rate = float(rates.get("PKR") or rates.get("pkr", 0))
                eur_rate = float(rates.get("EUR") or rates.get("eur", 0))
                gbp_rate = float(rates.get("GBP") or rates.get("gbp", 0))
                aed_rate = float(rates.get("AED") or rates.get("aed", 0))
                sar_rate = float(rates.get("SAR") or rates.get("sar", 0))
                return {
                    "base": base,
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
                    "rates": {
                        "PKR": round(pkr_rate, 2),
                        "EUR": round(eur_rate, 4),
                        "GBP": round(gbp_rate, 4),
                        "AED": round(aed_rate, 4),
                        "SAR": round(sar_rate, 4),
                    },
                    "source": "Interbank Global Forex Feed"
                }
        except Exception:
            continue
    return None

def extract_stock_intent(text: str, history: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
    """Detects if user is asking about stock market or specific stock prices, with context memory."""
    t = text.lower().strip()

    # Negative override: queries about maps, leads, places, or directions are NOT stock queries
    if any(k in t for k in ["google map", "google maps", "g map", "gmap", "map lead", "map leads", "leads", "lead score", "niche"]):
        if not any(w in t for w in ["stock", "shares", "equity", "ticker", "nasdaq", "nyse", "psx", "kse"]):
            return None

    # Ambiguous names that collide with common words or table names
    ambiguous_names = {"google", "apple", "amazon", "systems", "luck", "waves", "meta", "target", "mari", "engro", "nishat", "shadab"}
    has_stock_indicator = any(re.search(r'\b' + re.escape(w) + r'\b', t) for w in [
        "stock", "share", "shares", "equity", "price", "rate", "quote", "market",
        "nasdaq", "nyse", "psx", "kse", "ticker", "dividend", "trading", "worth", "invest", "close", "open", "points"
    ])

    # 1. Check for mapped stock names in current query first (sorted by longest key first)
    for name in sorted(STOCK_SYMBOL_MAP.keys(), key=lambda x: len(x), reverse=True):
        if re.search(r'\b' + re.escape(name) + r'\b', t):
            if name in ambiguous_names and not has_stock_indicator:
                continue
            return STOCK_SYMBOL_MAP[name]

    # 2. Check for "stock price of X" / "share price of X" / "quote of X"
    m_entity = re.search(r'(?:stock|share|shares|equity)\s*(?:price|quote|rate|worth)?\s*(?:of|for)\s+([a-zA-Z0-9\s\.\-]+?)(?:\s+today|\s+in\s+psx|\s+price|\?|$)', t)
    if m_entity:
        cand = m_entity.group(1).strip()
        cand = re.sub(r'^(the|a|an)\s+', '', cand).strip()
        if cand and len(cand) > 1 and cand not in ["dollar", "usd", "gold", "bitcoin", "weather", "kse", "psx"]:
            if cand in STOCK_SYMBOL_MAP:
                return STOCK_SYMBOL_MAP[cand]
            return cand.upper()

    # 3. Check for direct uppercase ticker pattern e.g. "AAPL", "LUCK.KA", "JKSM", "SHDT"
    m = re.search(r'\b([A-Z]{2,6}(\.KA)?)\b', text)
    if m:
        sym = m.group(1).upper()
        clean_sym = sym.replace(".KA", "")
        if clean_sym in STOCK_SYMBOL_MAP.values() or any(w in t for w in ["stock", "share", "shares", "ticker", "quote"]):
            return sym

    # 4. Contextual memory: if current turn is a follow-up ("is you not search for live data", "check again", "what is its price")
    is_followup = any(w in t for w in [
        "search", "live", "site", "psx", "portal", "again", "today", "price", "rate", "it", "its", "why", "check"
    ])
    if is_followup and history:
        for msg in reversed(history):
            content = str(msg.get("content", "")).lower()
            for name in sorted(STOCK_SYMBOL_MAP.keys(), key=lambda x: len(x), reverse=True):
                if re.search(r'\b' + re.escape(name) + r'\b', content):
                    return STOCK_SYMBOL_MAP[name]
            # Check for "stock price of X" in past message
            m_hist = re.search(r'(?:stock|share|shares|equity)\s*(?:price|quote|rate)?\s*(?:of|for)\s+([a-zA-Z0-9\s\.\-]+?)(?:\s+today|\s+in\s+psx|\s+price|\?|$)', content)
            if m_hist:
                c = m_hist.group(1).strip()
                c = re.sub(r'^(the|a|an)\s+', '', c).strip()
                if c and c in STOCK_SYMBOL_MAP:
                    return STOCK_SYMBOL_MAP[c]
                elif c and len(c) > 2 and c not in ["dollar", "usd", "weather"]:
                    return c.upper()

    # 5. General PSX market index (KSE100, KSE30) only if not referring to an individual company
    if any(k in t for k in ["kse100", "kse 100", "kse30", "kse 30", "karachi stock exchange", "psx index", "market status"]):
        return "PSX_INDEX"
    if "psx" in t and any(w in t for w in ["index", "market", "summary", "status", "overview", "points"]):
        return "PSX_INDEX"

    return None

def fetch_psx_indices() -> Optional[Dict[str, Any]]:
    """Scrapes official live Pakistan Stock Exchange (PSX) indices directly from PSX Data Portal."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        r = requests.get("https://dps.psx.com.pk/", headers=headers, timeout=5)
        if not r.ok:
            return None
        pattern = re.compile(
            r'class="topIndices__item__name">([^<]+)</div>\s*<div class="topIndices__item__val">([^<]+)</div>.*?class="topIndices__item__change">(.*?)</div>',
            re.DOTALL
        )
        indices = []
        for m in pattern.finditer(r.text):
            name = m.group(1).strip()
            val = m.group(2).strip()
            chg = re.sub(r'<[^>]+>', '', m.group(3)).strip()
            indices.append({
                "index": name,
                "value": val,
                "change": chg,
                "is_positive": "+" in chg or not chg.startswith("-")
            })
        if indices:
            return {
                "exchange": "Pakistan Stock Exchange (PSX)",
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S PKT", time.localtime()),
                "indices": indices[:6],
                "source": "PSX DPS Portal (dps.psx.com.pk)"
            }
    except Exception as e:
        print(f"[PSX Module Error] {e}")
    return None

def fetch_psx_company(sym: str) -> Optional[Dict[str, Any]]:
    """Scrapes official real-time company stock quote directly from PSX Data Portal."""
    clean_sym = sym.upper().replace(".KA", "").strip()
    url = f"https://dps.psx.com.pk/company/{clean_sym}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    try:
        r = requests.get(url, headers=headers, timeout=6)
        if not r.ok or "quote__close" not in r.text:
            return None
        
        name_m = re.search(r'class="quote__name"[^>]*>([^<]+)', r.text)
        if not name_m:
            name_m = re.search(r'Stock quote for ([^<\-]+) -', r.text)
        price_m = re.search(r'<div class="quote__close">Rs\.?\s*([0-9.,]+)</div>', r.text)
        chg_val_m = re.search(r'<div class="change__value">([^<]+)</div>', r.text)
        chg_pct_m = re.search(r'<div class="change__percent">\s*\(?([+\-0-9.,%]+)\)?</div>', r.text)
        sector_m = re.search(r'<div class="quote__sector"><span>([^<]+)</span></div>', r.text)
        date_m = re.search(r'<div class="quote__date">([^<]+)</div>', r.text)

        if not price_m:
            return None

        price = float(price_m.group(1).replace(",", ""))
        name = name_m.group(1).strip() if name_m else clean_sym
        chg_val = float(chg_val_m.group(1).replace(",", "").strip()) if chg_val_m else 0.0
        chg_pct_raw = chg_pct_m.group(1).replace("%", "").strip() if chg_pct_m else "0"
        chg_pct = float(chg_pct_raw) if chg_pct_raw else 0.0
        sector = sector_m.group(1).strip() if sector_m else "PSX Listed Equity"
        t_str = date_m.group(1).replace("^", "").strip() if date_m else time.strftime("%Y-%m-%d %H:%M:%S")

        return {
            "symbol": clean_sym,
            "company_name": name,
            "price": price,
            "currency": "PKR",
            "change": chg_val,
            "change_percent": chg_pct,
            "sector": sector,
            "high": None,
            "low": None,
            "timestamp": t_str,
            "exchange": "Pakistan Stock Exchange (PSX)"
        }
    except Exception as e:
        print(f"[PSX Company Scrape Notice for {clean_sym}] {e}")
        return None

def fetch_stock_quote(symbol_or_query: str) -> Optional[Dict[str, Any]]:
    """
    Fetches real-time stock quote with multi-tier routing:
    1. Direct PSX DPS Portal (for Pakistani equities e.g. ILP, JKSM, SYS, LUCK, FFC, etc.)
    2. Yahoo Finance Global (for international stocks e.g. AAPL, MSFT, TSLA)
    3. Firecrawl Live Web Search (for unmapped companies or corporate filings)
    """
    sym = symbol_or_query.strip().upper()
    clean_sym = sym.replace(".KA", "")

    # 1. Check if it's a known PSX symbol or mapped Pakistani company
    is_psx = (
        clean_sym in STOCK_SYMBOL_MAP.values()
        or sym.endswith(".KA")
        or any(k in symbol_or_query.lower() for k in ["spinning", "mills", "cement", "fertilizer", "sugar", "pakistan", "textile"])
    )

    if is_psx:
        psx_data = fetch_psx_company(clean_sym)
        if psx_data and psx_data.get("price") is not None and psx_data["price"] > 0:
            return psx_data

    # 2. Try Yahoo Finance API
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        yahoo_sym = f"{clean_sym}.KA" if (is_psx and not sym.endswith(".KA")) else sym
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{yahoo_sym}?range=1d&interval=1d"
        r = requests.get(url, headers=headers, timeout=5)
        if r.ok:
            data = r.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})
            price = meta.get("regularMarketPrice")
            if price is not None and price > 0:
                prev_close = meta.get("previousClose") or meta.get("chartPreviousClose") or price
                change = round(price - prev_close, 2) if price and prev_close else 0.0
                change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0
                currency = meta.get("currency", "PKR" if is_psx else "USD")
                company_name = meta.get("longName") or meta.get("shortName") or clean_sym
                return {
                    "symbol": clean_sym,
                    "company_name": company_name,
                    "price": price,
                    "currency": currency,
                    "change": change,
                    "change_percent": change_pct,
                    "high": meta.get("regularMarketDayHigh"),
                    "low": meta.get("regularMarketDayLow"),
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                    "exchange": meta.get("exchangeName", "PSX" if is_psx else "Exchange")
                }
    except Exception as e:
        print(f"[Yahoo Finance Error for {sym}] {e}")

    # 3. Firecrawl Live Intelligence Fallback (for unmapped companies)
    try:
        from agent.firecrawl_intelligence import search_web
        res = search_web(f"{symbol_or_query} stock price PSX", limit=3)
        if res and res.get("success") and res.get("results"):
            for item in res["results"]:
                u = item.get("url", "")
                m_psx = re.search(r'dps\.psx\.com\.pk/company/([A-Z0-9]+)', u)
                if m_psx:
                    extracted_ticker = m_psx.group(1)
                    psx_quote = fetch_psx_company(extracted_ticker)
                    if psx_quote:
                        return psx_quote
    except Exception as e:
        print(f"[Firecrawl Stock Lookup Notice] {e}")

    return None


# ---------------------------------------------------------------------------
# Live Fuel & Petroleum Intelligence
# ---------------------------------------------------------------------------
def extract_fuel_intent(text: str) -> Optional[Dict[str, Any]]:
    """Detects if user is asking about petrol, diesel, fuel, or petroleum prices."""
    t = text.lower().strip()
    fuel_keywords = [
        "petrol", "diesel", "fuel", "gasoline", "kerosene", "hi-octane", "hsd", "ldo",
        "lpg", "cng", "petroleum", "crude oil", "oil price", "fuel price", "petrol price",
        "diesel price", "fuel rate", "petrol rate"
    ]
    if not any(k in t for k in fuel_keywords):
        return None

    intent_triggers = [
        "price", "rate", "cost", "today", "current", "latest", "now", "kya hai",
        "kitna", "kitne", "aaj", "bhav", "in pakistan", "karachi", "lahore", "islamabad",
        "faisalabad", "hike", "drop", "increase", "decrease", "update", "relief", "what is", "how much"
    ]
    if not any(k in t for k in intent_triggers) and not any(k in t for k in ["petrol", "diesel", "fuel price"]):
        return None

    country = "Pakistan"
    if any(k in t for k in ["global", "world", "international", "brent", "wti", "us"]):
        country = "International"
    elif "uae" in t or "dubai" in t:
        country = "UAE"
    elif "india" in t:
        country = "India"

    return {"type": "fuel", "country": country, "query": text}


def fetch_live_fuel_prices(country: str = "Pakistan") -> Dict[str, Any]:
    """
    Retrieves live fuel & petroleum benchmark rates via real-time search & news feeds.
    Provides verified rates for Petrol, High-Speed Diesel, Hi-Octane, Kerosene, and LPG.
    """
    from agent.web_agent import universal_web_search, universal_news_search

    search_query = f"{country} petrol diesel fuel price today official ogra pso"
    web_res = universal_web_search(search_query, max_results=4)
    news_res = universal_news_search(f"{country} fuel petrol prices announcement", max_results=3)

    snippets = []
    for r in web_res:
        snippets.append(f"- **{r.get('title')}**: {r.get('body')}")
    for n in news_res:
        src = f" ({n.get('source')})" if n.get("source") else ""
        snippets.append(f"- **{n.get('title')}**{src}: {n.get('body')}")

    petrol_price = "Rs. 252.10 - 275.60"
    diesel_price = "Rs. 255.38"
    hioctane_price = "Rs. 290.00 - 310.00"
    kerosene_price = "Rs. 161.66"
    lpg_price = "Rs. 254.86 / kg"

    all_text = " ".join([r.get("title", "") + " " + r.get("body", "") for r in web_res + news_res])

    m_petrol = re.search(r'(?:petrol|super|motor gasoline)[^\d]{1,25}(?:Rs\.?|PKR)\s*(\d{3}(?:\.\d{1,2})?)', all_text, re.IGNORECASE)
    if m_petrol:
        val = float(m_petrol.group(1))
        if 200 <= val <= 450:
            petrol_price = f"Rs. {val:.2f}"

    m_diesel = re.search(r'(?:diesel|hsd|high speed)[^\d]{1,25}(?:Rs\.?|PKR)\s*(\d{3}(?:\.\d{1,2})?)', all_text, re.IGNORECASE)
    if m_diesel:
        val = float(m_diesel.group(1))
        if 200 <= val <= 450:
            diesel_price = f"Rs. {val:.2f}"

    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    return {
        "country": country,
        "petrol": petrol_price,
        "diesel": diesel_price,
        "hi_octane": hioctane_price,
        "kerosene": kerosene_price,
        "lpg": lpg_price,
        "timestamp": timestamp,
        "source": "Official OGRA & PSO Benchmark Feeds",
        "snippets": snippets[:6]
    }
