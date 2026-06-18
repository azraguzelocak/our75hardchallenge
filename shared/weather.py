"""Weather-aware outdoor workout helper (Open-Meteo, no API key needed).

The "one outdoor session" rule depends on the weather. This looks at today's
hourly forecast for our location and suggests the best dry window for the
outdoor workout, or flags that it'll rain and to plan an indoor backup.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

import httpx

from shared.config import load_settings

log = logging.getLogger("shared.weather")

_OPEN_METEO = "https://api.open-meteo.com/v1/forecast"

# Daylight window we'd consider for an outdoor session, and the rain threshold.
_DAY_START, _DAY_END = 7, 21
_DRY_PROB = 30  # % precipitation probability at or below this counts as "dry"


def suggest_outdoor() -> str:
    """Return a short sentence about the best dry window for an outdoor workout."""
    settings = load_settings()
    try:
        data = _fetch(settings.weather_latitude, settings.weather_longitude,
                      settings.weather_timezone)
    except Exception as exc:  # noqa: BLE001 - network/parse; degrade gracefully
        log.warning("Weather fetch failed: %s", exc)
        return ("Couldn't fetch the forecast — check it yourself and grab a dry "
                "window for your outdoor workout.")
    return _describe(data, settings.weather_timezone)


def _fetch(lat: float, lon: float, tz: str) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "precipitation_probability,temperature_2m",
        "timezone": tz,
        "forecast_days": 1,
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(_OPEN_METEO, params=params)
        resp.raise_for_status()
        return resp.json()


def _describe(data: dict, tz: str) -> str:
    hourly = data.get("hourly", {})
    times: list[str] = hourly.get("time", [])
    probs: list = hourly.get("precipitation_probability", [])
    temps: list = hourly.get("temperature_2m", [])

    today = dt.datetime.now(ZoneInfo(tz)).date().isoformat()

    # Collect daytime hours for today: (hour, prob, temp)
    slots: list[tuple[int, int, float]] = []
    for i, t in enumerate(times):
        if not t.startswith(today):
            continue
        hour = int(t[11:13])
        if not (_DAY_START <= hour < _DAY_END):
            continue
        prob = int(probs[i]) if i < len(probs) and probs[i] is not None else 0
        temp = float(temps[i]) if i < len(temps) and temps[i] is not None else 0.0
        slots.append((hour, prob, temp))

    if not slots:
        return "No forecast for today — grab any dry gap for your outdoor workout."

    # Find the longest contiguous run of dry hours.
    best: list[tuple[int, int, float]] = []
    run: list[tuple[int, int, float]] = []
    for slot in slots:
        if slot[1] <= _DRY_PROB:
            run.append(slot)
            if len(run) > len(best):
                best = run[:]
        else:
            run = []

    if best:
        start = best[0][0]
        end = best[-1][0] + 1
        avg_temp = round(sum(s[2] for s in best) / len(best))
        max_prob = max(s[1] for s in best)
        return (f"Best dry window for your outdoor workout: "
                f"{start:02d}:00–{end:02d}:00 (~{avg_temp}°C, {max_prob}% rain).")

    # Otherwise it's wet — point at the least-bad hour and suggest a backup.
    driest = min(slots, key=lambda s: s[1])
    return (f"Rain likely most of the day (driest around {driest[0]:02d}:00 at "
            f"{driest[1]}%). Plan an indoor backup and grab any gap you can.")
