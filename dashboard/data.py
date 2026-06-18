"""Data access for the dashboard.

Reads from the same Supabase database as the bot (via the shared client). Every
query is wrapped so a connection problem shows an empty state rather than
crashing the page. Results are lightly cached so the UI stays snappy.
"""

from __future__ import annotations

import logging

import pandas as pd
import streamlit as st

from shared import storage
from shared.db import get_client

log = logging.getLogger("dashboard.data")

# Reason the last load failed (surfaced in the UI to make setup issues obvious).
LAST_ERROR: str | None = None


def _table(name: str, user_id: str | None = None, order: str = "date") -> list[dict]:
    """Run a simple select, returning [] on any failure."""
    try:
        q = get_client().table(name).select("*")
        if user_id:
            q = q.eq("user_id", user_id)
        return q.order(order).execute().data
    except Exception as exc:  # noqa: BLE001 - dashboard should never hard-crash
        log.warning("Query on %s failed: %s", name, exc)
        return []


def users() -> list[dict]:
    """Load both users. Records the failure reason in LAST_ERROR for the UI."""
    global LAST_ERROR
    try:
        rows = get_client().table("users").select("*").order("slug").execute().data
        LAST_ERROR = None
        return rows
    except Exception as exc:  # noqa: BLE001
        LAST_ERROR = f"{type(exc).__name__}: {exc}"
        log.warning("Could not load users: %s", exc)
        return []


@st.cache_data(ttl=60)
def weights(user_id: str) -> pd.DataFrame:
    df = pd.DataFrame(_table("weights", user_id))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        for col in ("weight", "waist", "hips", "arms"):
            if col in df:
                df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


@st.cache_data(ttl=60)
def daily_logs(user_id: str) -> pd.DataFrame:
    df = pd.DataFrame(_table("daily_logs", user_id))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["day_passed"] = df["day_passed"].astype(bool)
    return df


@st.cache_data(ttl=60)
def meals(user_id: str) -> pd.DataFrame:
    df = pd.DataFrame(_table("meals", user_id))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        for col in ("ai_calories", "ai_protein", "ai_carbs", "ai_fat"):
            df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60)
def workouts(user_id: str) -> pd.DataFrame:
    df = pd.DataFrame(_table("workouts", user_id))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["is_outdoor"] = df["is_outdoor"].astype(bool)
        df["ai_calories_burned"] = pd.to_numeric(
            df.get("ai_calories_burned"), errors="coerce"
        ).fillna(0)
    return df


@st.cache_data(ttl=60)
def books(user_id: str) -> pd.DataFrame:
    return pd.DataFrame(_table("books", user_id, order="created_at"))


@st.cache_data(ttl=60)
def reading_logs(user_id: str) -> pd.DataFrame:
    df = pd.DataFrame(_table("reading_logs", user_id))
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df["pages_read"] = pd.to_numeric(df["pages_read"], errors="coerce").fillna(0)
    return df


@st.cache_data(ttl=60)
def water(user_id: str) -> pd.DataFrame:
    """Daily water intake (ml) from the checklist's water task completions."""
    logs = daily_logs(user_id)
    if logs.empty or "id" not in logs:
        return pd.DataFrame()
    id_to_date = dict(zip(logs["id"], logs["date"]))
    try:
        res = (
            get_client().table("task_completions")
            .select("daily_log_id, value")
            .eq("task_key", "water")
            .in_("daily_log_id", list(id_to_date))
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Water query failed: %s", exc)
        return pd.DataFrame()
    rows = [
        {"date": id_to_date[r["daily_log_id"]], "ml": float(r["value"] or 0)}
        for r in res.data if r["daily_log_id"] in id_to_date
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
    return df


@st.cache_data(ttl=60)
def progress_photos(user_id: str) -> list[dict]:
    """Progress photos (oldest first) with short-lived signed URLs."""
    rows = _table("progress_photos", user_id)
    out: list[dict] = []
    for r in rows:
        path = r.get("photo_path")
        url = storage.signed_url(path) if path else None
        out.append({"date": r["date"], "url": url})
    return out


# ---------------------------------------------------------------------------
# Derived helpers
# ---------------------------------------------------------------------------
def daily_nutrition(user_id: str) -> pd.DataFrame:
    """Calories + macros summed per day."""
    df = meals(user_id)
    if df.empty:
        return df
    grouped = (
        df.groupby(df["date"].dt.date)
        .agg(calories=("ai_calories", "sum"), protein=("ai_protein", "sum"),
             carbs=("ai_carbs", "sum"), fat=("ai_fat", "sum"))
        .reset_index()
        .rename(columns={"date": "day"})
    )
    grouped["day"] = pd.to_datetime(grouped["day"])
    return grouped


def overview(user_row: dict) -> dict:
    """Headline numbers for a user."""
    uid = user_row["id"]
    logs = daily_logs(uid)
    days_completed = int(logs["day_passed"].sum()) if not logs.empty else 0

    w = weights(uid)
    weight_change = None
    latest_weight = None
    if not w.empty and w["weight"].notna().any():
        valid = w.dropna(subset=["weight"])
        latest_weight = float(valid.iloc[-1]["weight"])
        start = user_row.get("start_weight") or float(valid.iloc[0]["weight"])
        weight_change = round(latest_weight - float(start), 1)

    return {
        "current_day": user_row.get("current_day", 1),
        "current_streak": user_row.get("current_streak", 0),
        "longest_streak": user_row.get("longest_streak", 0),
        "days_completed": days_completed,
        "latest_weight": latest_weight,
        "weight_change": weight_change,
        "goal_weight": user_row.get("goal_weight"),
        "start_weight": user_row.get("start_weight"),
    }
