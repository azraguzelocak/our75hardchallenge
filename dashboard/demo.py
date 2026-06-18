"""Demo data for the dashboard — lets it run with no database.

Exposes the same function names as ``dashboard.data`` so the app can switch to
it by rebinding the module. Useful for previewing the look and feel before
Supabase is connected (sample numbers, not real tracking).
"""

from __future__ import annotations

import pandas as pd

LAST_ERROR = None

_TODAY = pd.Timestamp.now().normalize()

# Two demo users (id == slug here for simplicity).
_USERS = [
    {"id": "azra", "slug": "azra", "name": "Azra", "current_day": 28,
     "current_streak": 28, "longest_streak": 28, "start_weight": 70.0,
     "goal_weight": 63.0, "daily_calorie_target": 1800, "protein_target": 130},
    {"id": "berrin", "slug": "berrin", "name": "Berrin", "current_day": 12,
     "current_streak": 12, "longest_streak": 15, "start_weight": 82.0,
     "goal_weight": 75.0, "daily_calorie_target": 2100, "protein_target": 150},
]

_DAYS = {"azra": 28, "berrin": 12}


def _dates(uid: str) -> pd.DatetimeIndex:
    n = _DAYS[uid]
    return pd.date_range(_TODAY - pd.Timedelta(days=n - 1), periods=n, freq="D")


def users() -> list[dict]:
    return _USERS


def _row(uid: str) -> dict:
    return next(u for u in _USERS if u["id"] == uid)


def weights(user_id: str) -> pd.DataFrame:
    d = _dates(user_id)
    r = _row(user_id)
    start = r["start_weight"]
    # Gentle downward trend with a little day-to-day noise.
    wiggle = [0.0, 0.3, -0.1, 0.2, -0.2, 0.1, -0.3]
    weight = [round(start - i * 0.09 + wiggle[i % len(wiggle)], 1) for i in range(len(d))]
    return pd.DataFrame({
        "date": d, "weight": weight,
        "waist": [round(82 - i * 0.05, 1) for i in range(len(d))],
        "hips": [round(98 - i * 0.04, 1) for i in range(len(d))],
        "arms": [round(34 - i * 0.01, 1) for i in range(len(d))],
    })


def daily_logs(user_id: str) -> pd.DataFrame:
    d = _dates(user_id)
    passed = [(i % 9 != 5) for i in range(len(d))]  # a couple of misses for variety
    return pd.DataFrame({
        "date": d, "day_passed": passed, "day_number": range(1, len(d) + 1),
    })


def meals(user_id: str) -> pd.DataFrame:
    d = _dates(user_id)
    names = ["Chicken & rice", "Greek yogurt bowl", "Salmon & greens", "Omelette"]
    return pd.DataFrame({
        "date": d,
        "ai_calories": [1700 + (i % 4) * 80 for i in range(len(d))],
        "ai_protein": [120 + (i % 3) * 10 for i in range(len(d))],
        "ai_carbs": [150 - (i % 4) * 10 for i in range(len(d))],
        "ai_fat": [50 + (i % 3) * 5 for i in range(len(d))],
        "description": [names[i % len(names)] for i in range(len(d))],
        "photo_path": [None] * len(d),
    })


def workouts(user_id: str) -> pd.DataFrame:
    indoor = ["Upper-body strength", "Leg day", "Core & mobility", "Spin class"]
    outdoor = ["5 km run", "Long walk", "Cycling", "Outdoor HIIT"]
    rows = []
    for i, day in enumerate(_dates(user_id)):
        rows.append({"date": day, "is_outdoor": False,
                     "description": indoor[i % len(indoor)],
                     "duration_min": 45, "ai_calories_burned": 380})
        rows.append({"date": day, "is_outdoor": True,
                     "description": outdoor[i % len(outdoor)],
                     "duration_min": 45, "ai_calories_burned": 420})
    return pd.DataFrame(rows)


def books(user_id: str) -> pd.DataFrame:
    title = "Atomic Habits" if user_id == "azra" else "Deep Work"
    current = 180 if user_id == "azra" else 90
    return pd.DataFrame([{
        "title": title, "total_pages": 320, "current_page": current, "is_finished": False,
    }])


def reading_logs(user_id: str) -> pd.DataFrame:
    d = _dates(user_id)
    pages = [10, 12, 15, 10, 11, 0, 20, 14, 10, 13, 10, 16]
    return pd.DataFrame({
        "date": d, "pages_read": [pages[i % len(pages)] for i in range(len(d))],
    })


def water(user_id: str) -> pd.DataFrame:
    d = _dates(user_id)
    # Mostly hitting the 3.8 L goal, with a couple of short days.
    pattern = [3900, 4000, 3600, 3800, 3200, 3850, 4100, 3000, 3800, 3950, 3700, 4000]
    return pd.DataFrame({"date": d, "ml": [pattern[i % len(pattern)] for i in range(len(d))]})


def progress_photos(user_id: str) -> list[dict]:
    # No real images in demo mode.
    return []


def daily_nutrition(user_id: str) -> pd.DataFrame:
    df = meals(user_id)
    g = (df.groupby(df["date"].dt.date)
         .agg(calories=("ai_calories", "sum"), protein=("ai_protein", "sum"),
              carbs=("ai_carbs", "sum"), fat=("ai_fat", "sum"))
         .reset_index().rename(columns={"date": "day"}))
    g["day"] = pd.to_datetime(g["day"])
    return g


def overview(user_row: dict) -> dict:
    uid = user_row["id"]
    logs = daily_logs(uid)
    w = weights(uid)
    latest = float(w.iloc[-1]["weight"])
    start = user_row["start_weight"]
    return {
        "current_day": user_row["current_day"],
        "current_streak": user_row["current_streak"],
        "longest_streak": user_row["longest_streak"],
        "days_completed": int(logs["day_passed"].sum()),
        "latest_weight": latest,
        "weight_change": round(latest - start, 1),
        "goal_weight": user_row["goal_weight"],
        "start_weight": start,
    }
