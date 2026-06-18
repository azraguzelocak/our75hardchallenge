"""The AI brain — Anthropic API calls (vision + text).

All functions here are synchronous; the bot calls them via asyncio.to_thread
so they don't block the event loop. They read the model + key from settings.

We use `claude-sonnet-4-6` (the model named in the brief — vision-capable).
Structured results are requested as JSON in the prompt and parsed defensively,
which keeps us compatible with the pinned SDK version.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from functools import lru_cache

import anthropic

from shared.config import load_settings

log = logging.getLogger("bot.ai")


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    settings = load_settings()
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _model() -> str:
    return load_settings().anthropic_model


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _text_of(response) -> str:
    """Join the text blocks of a Messages response."""
    return "".join(b.text for b in response.content if b.type == "text").strip()


def _parse_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply, defensively."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
    raise ValueError(f"Could not parse JSON from model reply: {text[:200]}")


def _image_block(image_bytes: bytes, media_type: str = "image/jpeg") -> dict:
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(image_bytes).decode("utf-8"),
        },
    }


# ---------------------------------------------------------------------------
# Food photo -> calories + macros
# ---------------------------------------------------------------------------
_MEAL_PROMPT = (
    "You are a nutrition estimator. Look at this meal photo and estimate its "
    "nutrition. Reply with ONLY a JSON object, no prose, with these keys:\n"
    '  "description": short sentence-case name of the meal,\n'
    '  "calories": integer kcal,\n'
    '  "protein_g": grams of protein (number),\n'
    '  "carbs_g": grams of carbohydrate (number),\n'
    '  "fat_g": grams of fat (number).\n'
    "Estimate realistic single-serving values. If the caption adds detail, use it."
)


def estimate_meal(image_bytes: bytes, *, caption: str | None = None,
                  media_type: str = "image/jpeg") -> dict:
    """Estimate calories + macros from a meal photo. Returns a normalized dict."""
    content = [_image_block(image_bytes, media_type)]
    prompt = _MEAL_PROMPT
    if caption:
        prompt += f"\nUser caption: {caption}"
    content.append({"type": "text", "text": prompt})

    response = _client().messages.create(
        model=_model(),
        max_tokens=512,
        messages=[{"role": "user", "content": content}],
    )
    data = _parse_json(_text_of(response))
    return {
        "description": str(data.get("description") or (caption or "Meal")).strip(),
        "calories": int(round(float(data.get("calories", 0)))),
        "protein": round(float(data.get("protein_g", 0)), 1),
        "carbs": round(float(data.get("carbs_g", 0)), 1),
        "fat": round(float(data.get("fat_g", 0)), 1),
    }


# ---------------------------------------------------------------------------
# Workout (text or photo) -> calories burned + duration + outdoor guess
# ---------------------------------------------------------------------------
_WORKOUT_PROMPT = (
    "You are a fitness estimator. From the workout described below (and/or the "
    "photo), estimate the session. Reply with ONLY a JSON object, no prose:\n"
    '  "summary": short sentence-case description of the workout,\n'
    '  "duration_min": integer minutes,\n'
    '  "calories_burned": integer kcal,\n'
    '  "is_outdoor": true/false (best guess whether it was outdoors).\n'
    "Assume an adult of average build if unspecified."
)


def estimate_workout(*, description: str | None = None, image_bytes: bytes | None = None,
                     media_type: str = "image/jpeg") -> dict:
    """Estimate a workout from a text description and/or a photo."""
    content: list[dict] = []
    if image_bytes:
        content.append(_image_block(image_bytes, media_type))
    prompt = _WORKOUT_PROMPT
    if description:
        prompt += f"\nWorkout: {description}"
    content.append({"type": "text", "text": prompt})

    response = _client().messages.create(
        model=_model(),
        max_tokens=512,
        messages=[{"role": "user", "content": content}],
    )
    data = _parse_json(_text_of(response))
    return {
        "summary": str(data.get("summary") or (description or "Workout")).strip(),
        "duration_min": int(round(float(data.get("duration_min", 45)))),
        "calories_burned": int(round(float(data.get("calories_burned", 0)))),
        "is_outdoor": bool(data.get("is_outdoor", False)),
    }


# ---------------------------------------------------------------------------
# Workout suggestion (/workout)
# ---------------------------------------------------------------------------
def suggest_workout(*, name: str, recent: list[str]) -> str:
    """Generate today's plan: one indoor + one outdoor, rotating muscle groups."""
    recent_text = "\n".join(f"- {r}" for r in recent) if recent else "- (none logged)"
    prompt = (
        f"Plan today's two 75 Hard workouts for {name}. The goal is fat loss plus "
        f"strength. Give exactly one indoor session and one outdoor session, each "
        f"about 45 minutes. Rotate muscle groups so we don't hit the same group two "
        f"days running. Here are the recent sessions:\n{recent_text}\n\n"
        f"Keep it SHORT. Two lines only:\n"
        f"Indoor: <focus> — 3 exercises, comma-separated\n"
        f"Outdoor: <focus> — one line\n"
        f"Then one short hard-hitting line. Tone: blunt, demanding, motivating — "
        f"a drill-sergeant coach, zero excuses. No fluff, no cute emoji."
    )
    response = _client().messages.create(
        model=_model(),
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return _text_of(response)


# ---------------------------------------------------------------------------
# Daily summary (/summary, and the scheduled end-of-day post in phase 5)
# ---------------------------------------------------------------------------
def daily_summary(*, name: str, day_number: int, tasks_done: int, tasks_total: int,
                  day_passed: bool, calories: int, calorie_target: int | None,
                  workouts: int, pending: list[str]) -> str:
    """Write a short, supportive end-of-day summary."""
    target_line = (
        f"calorie target {calorie_target}" if calorie_target else "no calorie target set"
    )
    pending_line = ", ".join(pending) if pending else "nothing — all done"
    prompt = (
        f"Write a short, warm end-of-day 75 Hard summary for {name}, day "
        f"{day_number} of 75. Facts:\n"
        f"- tasks completed: {tasks_done}/{tasks_total}\n"
        f"- day passed: {day_passed}\n"
        f"- calories eaten today: {calories} ({target_line})\n"
        f"- workouts logged: {workouts}\n"
        f"- still pending: {pending_line}\n\n"
        f"Max two short lines. Tone: blunt, hard, motivating — a drill-sergeant "
        f"coach. Day done = hard-earned respect; tasks pending = call out the slack "
        f"and demand better (never attack their worth). No fluff, minimal emoji."
    )
    response = _client().messages.create(
        model=_model(),
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}],
    )
    return _text_of(response)
