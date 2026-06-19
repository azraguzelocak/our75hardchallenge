"""The 75 Hard coach brain — context, trends, preferences, system prompt.

Lives in `shared` (not `dashboard`) so both the Streamlit dashboard and the
Telegram bot can use it without pulling in Streamlit. Everything is scoped to a
user slug; reads go through shared.db.
"""

from __future__ import annotations

import datetime as dt

from shared import db
from shared.config import USERS, TaskKind


# ---------------------------------------------------------------------------
# Weekly trend (this week vs the week before)
# ---------------------------------------------------------------------------
def week_summary(user_id: str) -> str:
    today = db.app_today()
    this_a, this_b = today - dt.timedelta(days=6), today
    last_a, last_b = today - dt.timedelta(days=13), today - dt.timedelta(days=7)

    def _d(s):  # parse ISO date string -> date
        return dt.date.fromisoformat(str(s)[:10])

    def _count(rows, a, b):
        return sum(1 for r in rows if a <= _d(r["date"]) <= b)

    def _avg(pairs, a, b):  # pairs: list[(date, value)]
        vals = [v for d, v in pairs if a <= d <= b]
        return round(sum(vals) / len(vals)) if vals else None

    wo = db.get_recent_workouts(user_id, 14)
    wo_this, wo_last = _count(wo, this_a, this_b), _count(wo, last_a, last_b)

    # calories per day (sum meals by day, then average across days in window)
    cal_by_day: dict[dt.date, float] = {}
    for m in db.get_meal_rows(user_id, 14):
        cal_by_day[_d(m["date"])] = cal_by_day.get(_d(m["date"]), 0) + (m.get("ai_calories") or 0)
    cal_pairs = list(cal_by_day.items())
    cal_this, cal_last = _avg(cal_pairs, this_a, this_b), _avg(cal_pairs, last_a, last_b)

    pg_by_day: dict[dt.date, float] = {}
    for r in db.get_reading_rows(user_id, 14):
        pg_by_day[_d(r["date"])] = pg_by_day.get(_d(r["date"]), 0) + (r.get("pages_read") or 0)
    pg_pairs = list(pg_by_day.items())
    pg_this, pg_last = _avg(pg_pairs, this_a, this_b), _avg(pg_pairs, last_a, last_b)

    weights = [w for w in db.get_weights(user_id) if w.get("weight") is not None]
    weight_line = "no weight logged"
    if weights:
        now_w = float(weights[-1]["weight"])
        prior = [w for w in weights if _d(w["date"]) <= last_b]
        if prior:
            delta = round(now_w - float(prior[-1]["weight"]), 1)
            weight_line = f"{now_w} kg ({delta:+} kg vs a week ago)"
        else:
            weight_line = f"{now_w} kg"

    def _trend(this, last, unit=""):
        if this is None:
            return "n/a"
        if last is None:
            return f"{this}{unit}"
        return f"{this}{unit} (was {last}{unit})"

    return (
        f"workouts {wo_this} (was {wo_last}) · "
        f"avg calories {_trend(cal_this, cal_last)} · "
        f"avg pages/day {_trend(pg_this, pg_last)} · "
        f"weight {weight_line}"
    )


# ---------------------------------------------------------------------------
# Current-day context
# ---------------------------------------------------------------------------
def _task(state, key):
    return next((t for t in state.tasks if t.task.key == key), None)


def build_context(slug: str) -> str:
    user = USERS[slug]
    state = db.get_checklist_state(user)
    row = state.user_row
    uid = row["id"]
    today = db.app_today()

    done = [t.task.label for t in state.tasks if t.complete]
    pending = [t.task.label for t in state.tasks if not t.complete]

    # Workout slots (need 2, at least 1 outdoor)
    total_wo, outdoor_wo = db.get_workout_counts(uid, today)
    wo_need = []
    if total_wo < 2:
        wo_need.append(f"{2 - total_wo} more session(s)")
    if outdoor_wo < 1:
        wo_need.append("one must be outdoors")
    wo_line = "done" if not wo_need else ", ".join(wo_need)

    # Water remaining
    water = _task(state, "water")
    water_line = "n/a"
    if water:
        cur = int(water.value or 0)
        tgt = int(water.task.target or 0)
        water_line = "done" if water.complete else f"{cur}/{tgt} ml ({tgt - cur} ml left)"

    # Nutrition vs targets
    totals = db.day_nutrition_totals(uid, today)
    cal_t = row.get("daily_calorie_target")
    pro_t = row.get("protein_target")
    protein_now = round(totals["protein"])
    protein_gap = (f", {max(0, pro_t - protein_now)} g protein to go" if pro_t else "")

    # Yesterday's verdict
    y_log = db.get_daily_log(uid, today - dt.timedelta(days=1))
    yesterday = "no data"
    if y_log:
        yesterday = "passed ✅" if y_log.get("day_passed") else "missed ❌"

    # Weight + book
    latest = db.latest_weight(uid)
    weight_now = latest.get("weight") if latest else None
    books = db.get_books(uid)
    book_line = "none"
    if books:
        active = [b for b in books if not b.get("is_finished")] or books
        b = active[-1]
        book_line = f"{b['title']} ({b.get('current_page') or 0}/{b.get('total_pages') or '?'} pages)"

    prefs = db.get_coach_profile(uid)
    day_str = "warm-up (not started)" if state.day_number == 0 else f"day {state.day_number} of 75"

    lines = [
        f"Name: {user.name}",
        f"Status: {day_str}, current streak {row.get('current_streak', 0)}. "
        f"Yesterday: {yesterday}.",
        f"Today: {len(done)}/{state.total_count} tasks done."
        + (f" Pending: {', '.join(pending)}." if pending else " All done!"),
        f"Workouts today: {total_wo}/2 ({outdoor_wo} outdoor) — {wo_line}.",
        f"Water: {water_line}.",
        f"Nutrition today: {totals['calories']} kcal"
        + (f" / {cal_t} target" if cal_t else "")
        + f", {protein_now} g protein" + protein_gap + ".",
        f"Weight: {weight_now if weight_now is not None else 'not logged'} kg"
        + (f", goal {row['goal_weight']}" if row.get("goal_weight") else "") + ".",
        f"Current book: {book_line}.",
        f"This week vs last: {week_summary(uid)}.",
    ]
    if prefs:
        lines.append(f"Remembered preferences:\n{prefs}")
    return "\n".join(lines)


def opening_line(slug: str) -> str:
    """A short proactive status shown when the chat opens (no API call)."""
    user = USERS[slug]
    state = db.get_checklist_state(user)
    pending = [t.task.label for t in state.tasks if not t.complete]
    if not pending:
        return f"Nice work, {user.name} — everything's done today. 💪 Ask me anything."
    bits = []
    water = _task(state, "water")
    if water and not water.complete:
        cur, tgt = int(water.value or 0), int(water.task.target or 0)
        bits.append(f"water {tgt - cur} ml short")
    total_wo, outdoor_wo = db.get_workout_counts(state.user_row["id"], db.app_today())
    if total_wo < 2:
        bits.append(f"{2 - total_wo} workout(s) left" + (" (need outdoor)" if outdoor_wo < 1 else ""))
    extra = f" — {', '.join(bits)}" if bits else ""
    return (f"Hey {user.name}, {len(pending)} task(s) left today{extra}. "
            f"Want a plan, or shall I log something?")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------
def system_prompt(slug: str) -> str:
    user = USERS[slug]
    context = build_context(slug)
    task_keys = ", ".join(t.key for t in user.tasks)
    return (
        f"You are a supportive, practical 75 Hard coach for {user.name}. "
        f"You can see {user.name}'s real tracker data (below) and should answer "
        f"about their actual numbers — be specific.\n\n"
        f"Rules:\n"
        f"- Base nutrition/workout advice on the targets {user.name} set for "
        f"themselves. Steer toward consistency and hitting protein, not the "
        f"lowest calorie number. Encouraging tone.\n"
        f"- You can ONLY see and act on {user.name}'s own data. Never reference "
        f"anyone else's data.\n"
        f"- Never reveal secrets, API keys, passwords, env variables, or system "
        f"internals — even if asked.\n"
        f"- Tools: log data when asked (task done, weight, meal, workout, "
        f"reading), get_trends, and `remember` to save a lasting preference "
        f"(dietary needs, injuries, likes, goals). Only log/remember what they "
        f"ask. Confirm what you did. Valid task_key values: {task_keys}.\n"
        f"- If a logging request is missing key details (duration, indoor/"
        f"outdoor, calories, which task, pages, weight), ask ONE short clarifying "
        f"question first. Don't over-ask when it's already clear.\n"
        f"- You cannot delete data or reset the day/streak — point them to the "
        f"dashboard's Settings confirm button for that.\n"
        f"- Keep replies concise and actionable.\n\n"
        f"{user.name}'s current data:\n{context}"
    )
