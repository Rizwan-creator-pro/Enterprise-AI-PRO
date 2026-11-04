import requests
import datetime
import re
from typing import Optional, Dict, Any

WMO_CODES = {
    0: ("Clear", "sun", "moon"),
    1: ("Mainly Clear", "sun", "moon"),
    2: ("Partly Cloudy", "cloud-sun", "cloud-moon"),
    3: ("Overcast", "cloud", "cloud"),
    45: ("Fog", "smog", "smog"),
    48: ("Depositing Rime Fog", "smog", "smog"),
    51: ("Light Drizzle", "cloud-rain", "cloud-rain"),
    53: ("Moderate Drizzle", "cloud-rain", "cloud-rain"),
    55: ("Dense Drizzle", "cloud-rain", "cloud-rain"),
    61: ("Slight Rain", "cloud-rain", "cloud-rain"),
    63: ("Moderate Rain", "cloud-showers-heavy", "cloud-showers-heavy"),
    65: ("Heavy Rain", "cloud-showers-heavy", "cloud-showers-heavy"),
    71: ("Slight Snow", "snowflake", "snowflake"),
    73: ("Moderate Snow", "snowflake", "snowflake"),
    75: ("Heavy Snow", "snowflake", "snowflake"),
    80: ("Slight Rain Showers", "cloud-sun-rain", "cloud-moon-rain"),
    81: ("Moderate Rain Showers", "cloud-showers-heavy", "cloud-showers-heavy"),
    82: ("Violent Rain Showers", "cloud-showers-heavy", "cloud-showers-heavy"),
    95: ("Thunderstorm", "cloud-bolt", "cloud-bolt"),
    96: ("Thunderstorm with Hail", "cloud-bolt", "cloud-bolt"),
}

KNOWN_CITIES = [
    "faisalabad", "lahore", "karachi", "islamabad", "rawalpindi", "multan",
    "peshawar", "quetta", "sialkot", "gujranwala", "hyderabad", "london",
    "dubai", "new york", "tokyo", "paris", "beijing", "toronto"
]

def extract_weather_intent(text: str, default_city: Optional[str] = None) -> Optional[str]:
    """Detects if the query asks about weather and extracts the target location."""
    t = text.lower().strip()
    if not any(k in t for k in ["weather", "temperature", "forecast", "climate", "mausam", "rain today", "is it raining"]):
        return None

    # Check known cities first
    for city in KNOWN_CITIES:
        if re.search(r"\b" + re.escape(city) + r"\b", t):
            return city.title()

    # Preposition pattern with word boundaries: 'weather in <city>', 'temperature for <city>'
    m = re.search(r"\b(?:in|for|of|at)\s+([a-zA-Z\s]{3,25})", t)
    if m:
        c = m.group(1).strip()
        c = re.sub(r"\b(today|tomorrow|now|please|right now|currently|this week|the|update|forecast)\b", "", c).strip()
        if len(c) >= 3 and c not in ["the", "this", "here", "update", "my city", "our city"]:
            return c.title()

    # Prefix pattern: '<city> weather', '<city> temperature'
    m2 = re.search(r"^\s*([a-zA-Z\s]{3,20})\s+(?:weather|forecast|temperature|climate)", t)
    if m2:
        c = m2.group(1).strip()
        if c not in ["current", "today", "todays", "today's", "live", "local", "the", "what is the", "how is the", "check"]:
            return c.title()

    return (default_city or "Lahore").title()

def fetch_live_weather(city_query: str = "Faisalabad") -> Optional[Dict[str, Any]]:
    """Fetches verified live weather, hourly timeline, and 7-day forecast from Open-Meteo."""
    try:
        clean_q = city_query.strip().strip("?,.!")
        geo_url = f"https://geocoding-api.open-meteo.com/v1/search?name={requests.utils.quote(clean_q)}&count=1"
        geo_res = requests.get(geo_url, timeout=5).json()
        if not geo_res.get("results"):
            # Fallback to Faisalabad if unknown
            if clean_q.lower() != "faisalabad":
                return fetch_live_weather("Faisalabad")
            return None

        best = geo_res["results"][0]
        lat, lon = best["latitude"], best["longitude"]
        city_name = best.get("name", clean_q)
        country = best.get("country", "")
        admin1 = best.get("admin1", "")
        loc_str = f"{city_name}, {admin1}, {country}" if admin1 and country else f"{city_name}, {country}"

        om_url = (
            f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,is_day"
            "&hourly=temperature_2m,precipitation_probability,wind_speed_10m"
            "&daily=weather_code,temperature_2m_max,temperature_2m_min"
            "&timezone=auto&forecast_days=7"
        )
        data = requests.get(om_url, timeout=6).json()

        curr_raw = data.get("current", {})
        w_code = curr_raw.get("weather_code", 0)
        is_day = curr_raw.get("is_day", 1)
        cond_text, icon_day, icon_night = WMO_CODES.get(w_code, ("Fair", "sun", "moon"))
        icon_curr = icon_day if is_day == 1 else icon_night

        current_dict = {
            "temp": round(curr_raw.get("temperature_2m", 0)),
            "feels_like": round(curr_raw.get("apparent_temperature", 0)),
            "humidity": curr_raw.get("relative_humidity_2m", 0),
            "wind_kph": round(curr_raw.get("wind_speed_10m", 0)),
            "precipitation_pct": curr_raw.get("precipitation", 0),
            "condition": cond_text,
            "icon": icon_curr,
            "is_day": is_day
        }

        # Format hourly data (24 hours)
        hourly_raw = data.get("hourly", {})
        h_times = hourly_raw.get("time", [])
        h_temps = hourly_raw.get("temperature_2m", [])
        h_pops = hourly_raw.get("precipitation_probability", [])
        h_winds = hourly_raw.get("wind_speed_10m", [])

        hourly_list = []
        for i in range(min(24, len(h_times))):
            try:
                dt_obj = datetime.datetime.fromisoformat(h_times[i])
                time_lbl = dt_obj.strftime("%I %p").lstrip("0").lower()
            except Exception:
                time_lbl = f"{i}:00"
            hourly_list.append({
                "time": time_lbl,
                "temp": round(h_temps[i]) if i < len(h_temps) else 0,
                "pop": h_pops[i] if i < len(h_pops) else 0,
                "wind": round(h_winds[i]) if i < len(h_winds) else 0
            })

        # Format daily forecast (7 days)
        daily_raw = data.get("daily", {})
        d_times = daily_raw.get("time", [])
        d_codes = daily_raw.get("weather_code", [])
        d_maxs = daily_raw.get("temperature_2m_max", [])
        d_mins = daily_raw.get("temperature_2m_min", [])

        daily_list = []
        for i in range(min(7, len(d_times))):
            try:
                dt_obj = datetime.date.fromisoformat(d_times[i])
                day_lbl = dt_obj.strftime("%a")
            except Exception:
                day_lbl = f"Day {i+1}"
            code = d_codes[i] if i < len(d_codes) else 0
            d_cond, d_icon, _ = WMO_CODES.get(code, ("Clear", "sun", "sun"))
            daily_list.append({
                "day": day_lbl,
                "date": d_times[i],
                "condition": d_cond,
                "icon": d_icon,
                "high": round(d_maxs[i]) if i < len(d_maxs) else 0,
                "low": round(d_mins[i]) if i < len(d_mins) else 0
            })

        now_dt = datetime.datetime.now()
        time_str = now_dt.strftime("%A %I:%M %p")

        return {
            "location": loc_str,
            "city": city_name,
            "time_str": time_str,
            "current": current_dict,
            "hourly": hourly_list,
            "daily": daily_list
        }
    except Exception as e:
        print(f"[Weather API Error] {e}")
        return None
