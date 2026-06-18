"""Scheduled reminders via the bot's JobQueue (phase 5).

Daily nudges fire at the times in shared.config.DEFAULT_REMINDERS (local to the
configured timezone). Each reminder is tailored to what's still pending, so we
don't nag about tasks already done. A midnight rollover finalizes the previous
day and sends a celebration or a supportive reset message.

A /preview command lets you trigger any reminder on demand for testing.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import Application, ContextTypes

from bot import ai
from bot.auth import restricted
from shared import db, weather
from shared.config import (
    DEFAULT_REMINDERS,
    USERS,
    Mode,
    UserConfig,
    load_settings,
    the_other_user,
)
from shared.db import ChecklistState

log = logging.getLogger("bot.reminders")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _task_state(state: ChecklistState, key: str):
    return next((ts for ts in state.tasks if ts.task.key == key), None)


def _checklist_lines(state: ChecklistState) -> str:
    return "\n".join(
        f"{'✅' if ts.complete else '⬜'} {ts.task.label}" for ts in state.tasks
    )


# ---------------------------------------------------------------------------
# Message variations — a different phrasing fires each time (random pick).
# Placeholders are filled with .format(...).
# ---------------------------------------------------------------------------
_MORNING_OPENERS = [
    "🔥 Up, {name}. {day}",
    "🔥 No snooze, {name}. {day}",
    "🔥 Move, {name}. {day}",
    "🔥 Let's go, {name}. {day}",
    "🔥 Eyes open, {name}. {day}",
]
_DAY_LINES = [
    "Day {n} of 75. No days off.",
    "Day {n} of 75. Earn it.",
    "Day {n} of 75. Nobody's coming to do this for you.",
    "Day {n} of 75. Prove it again.",
]
_WATER_LINES = [
    "💧 {name}: {current}/{target} ml — behind. {left} to go. Drink, no stalling.",
    "💧 {left} ml short, {name}. {current}/{target}. Pick up the bottle — now.",
    "💧 Water's not optional, {name}. {current}/{target} ml, {left} to go.",
    "💧 {name}, {current}/{target} ml. Stop slacking and hydrate — {left} left.",
    "💧 Hydrate, {name}. {current}/{target} ml — {left} more. Go.",
]
_WORKOUT_LINES = [
    "🏋️ {name}: {total}/2 workouts ({note}). Finish it — no excuses.",
    "🏋️ {total}/2, {name}. {note}. Get moving — clock's running.",
    "🏋️ Second workout, {name}? {total}/2 done, {note}. No couch.",
    "🏋️ {name}, not done: {total}/2 ({note}). Lace up.",
]
_EVENING_OPENERS = [
    "🌆 {name}, day's not done — don't coast.",
    "🌆 Don't slack now, {name}. Close it out.",
    "🌆 {name}, finish strong. The day's not over.",
    "🌆 No coasting, {name}. Lock in the rest.",
]
_EVENING_PHOTO = [
    "📸 Progress photo now — {other} will see it.",
    "📸 Snap the progress photo. {other}'s watching.",
    "📸 Photo time, no skipping — {other} sees it.",
]
_EVENING_WEIGH = [
    "⚖️ Weigh in: /weight.",
    "⚖️ On the scale: /weight.",
    "⚖️ Log your weight: /weight. Face it.",
]
_FINAL_OPENERS = [
    "⏰ {name}, still not done:",
    "⏰ Clock's almost out, {name}. Outstanding:",
    "⏰ {name}, midnight's close and these are open:",
]
_FINAL_PUNCH = [
    "Miss these = back to DAY 1. Move. 🔥",
    "Leave these and the streak dies. Move. 🔥",
    "These aren't suggestions. Skip them, restart at day 1. Go. 🔥",
    "Don't throw the whole thing away now. Finish them. 🔥",
]


# ---------------------------------------------------------------------------
# Reminder builders — each returns the message text, or None to stay quiet
# ---------------------------------------------------------------------------
async def _morning(user: UserConfig, state: ChecklistState) -> str:
    settings = load_settings()
    forecast = await asyncio.to_thread(weather.suggest_outdoor)
    recent = await asyncio.to_thread(db.get_recent_workouts, state.user_row["id"], 3)
    recent_desc = [
        f"{r['date']}: {r.get('description') or 'workout'}"
        f"{' (outdoor)' if r.get('is_outdoor') else ''}"
        for r in recent
    ]
    plan = await asyncio.to_thread(ai.suggest_workout, name=user.name, recent=recent_desc)
    day_line = (
        "Warm-up day. Move." if state.day_number == 0
        else random.choice(_DAY_LINES).format(n=state.day_number)
    )
    opener = random.choice(_MORNING_OPENERS).format(name=user.name, day=day_line)
    return (
        f"{opener}\n\n"
        f"{_checklist_lines(state)}\n\n"
        f"🌤 {forecast}\n\n"
        f"🏋️ Today:\n{plan}"
    )


async def _water(user: UserConfig, state: ChecklistState) -> str | None:
    ts = _task_state(state, "water")
    if ts is None or ts.complete:
        return None
    current = int(ts.value or 0)
    target = int(ts.task.target or 0)
    return random.choice(_WATER_LINES).format(
        name=user.name, current=current, target=target, left=target - current
    )


async def _late_afternoon(user: UserConfig, state: ChecklistState) -> str | None:
    ts = _task_state(state, "workouts")
    if ts is None or ts.complete:
        return None
    total, outdoor = await asyncio.to_thread(
        db.get_workout_counts, state.user_row["id"], db.app_today()
    )
    note = "one still outdoors" if outdoor < 1 else "outdoor done"
    return random.choice(_WORKOUT_LINES).format(
        name=user.name, total=total, note=note
    )


async def _evening(user: UserConfig, state: ChecklistState) -> str:
    photo = _task_state(state, "progress_photo")
    lines = [random.choice(_EVENING_OPENERS).format(name=user.name)]
    if photo and not photo.complete:
        other = the_other_user(user.slug)
        lines.append(random.choice(_EVENING_PHOTO).format(other=other.name))
    lines.append(random.choice(_EVENING_WEIGH))
    return "\n".join(lines)


async def _night(user: UserConfig, state: ChecklistState) -> str:
    totals = await asyncio.to_thread(db.day_nutrition_totals, state.user_row["id"], db.app_today())
    total_workouts, _ = await asyncio.to_thread(
        db.get_workout_counts, state.user_row["id"], db.app_today()
    )
    pending = [ts.task.label for ts in state.tasks if not ts.complete]
    summary = await asyncio.to_thread(
        ai.daily_summary,
        name=user.name, day_number=state.day_number,
        tasks_done=state.completed_count, tasks_total=state.total_count,
        day_passed=state.all_complete, calories=totals["calories"],
        calorie_target=state.user_row.get("daily_calorie_target"),
        workouts=total_workouts, pending=pending,
    )
    extras = ["📖 Read your 10 pages."]
    if user.task("sleep"):
        extras.append("🛌 Log last night's sleep on the checklist.")
    if user.task("cigarettes"):
        extras.append("🚬 Log today's cigarette count on the checklist.")
    reading = _task_state(state, "reading")
    if reading and reading.complete:
        extras = [e for e in extras if not e.startswith("📖")]
    return summary + "\n\n" + "\n".join(extras)


async def _final(user: UserConfig, state: ChecklistState) -> str | None:
    pending = [ts.task.label for ts in state.tasks if not ts.complete]
    if not pending:
        return None
    bullet = "\n".join(f"• {p}" for p in pending)
    opener = random.choice(_FINAL_OPENERS).format(name=user.name)
    punch = random.choice(_FINAL_PUNCH)
    return f"{opener}\n{bullet}\n\n{punch}"


# Maps reminder key -> builder coroutine.
_BUILDERS = {
    "morning": _morning,
    "midday": _water,
    "afternoon": _water,
    "late_afternoon": _late_afternoon,
    "evening": _evening,
    "night": _night,
    "final": _final,
}


# ---------------------------------------------------------------------------
# Job callbacks
# ---------------------------------------------------------------------------
async def _run_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generic reminder job: build the message for its key and send it."""
    data = context.job.data
    slug, key = data["slug"], data["key"]
    user = USERS[slug]
    try:
        state = await asyncio.to_thread(db.get_checklist_state, user)
        text = await _BUILDERS[key](user, state)
        if text:
            await context.bot.send_message(chat_id=context.job.chat_id, text=text)
    except Exception:  # noqa: BLE001 - one bad reminder shouldn't kill the job queue
        log.exception("Reminder '%s' for %s failed", key, slug)


async def _run_rollover(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Just after midnight: finalize yesterday and send the verdict message."""
    slug = context.job.data["slug"]
    try:
        results = await asyncio.to_thread(db.finalize_due_days, slug)
    except Exception:  # noqa: BLE001
        log.exception("Rollover for %s failed", slug)
        return

    user = USERS[slug]
    for result in results:
        if result.day_number == 0:
            continue  # warm-up day before the challenge started — no verdict
        await context.bot.send_message(
            chat_id=context.job.chat_id, text=_verdict_message(user, result)
        )


def _verdict_message(user: UserConfig, result) -> str:
    if result.passed:
        return (f"🎉 Day {result.day_number} complete, {user.name}! "
                f"Current streak: {result.new_streak}. Onto day "
                f"{result.new_current_day} — let's go. 💪")
    if result.was_reset:  # strict mode
        return (f"Day {result.day_number} didn't close out fully, {user.name}, so "
                f"75 Hard resets to day 1.\n\nThe reset is the challenge — the point "
                f"is doing it anyway. Yesterday is data, not a verdict on you. "
                f"Recommit today and start strong. I've got you. 💛")
    # soft mode miss
    return (f"Day {result.day_number} had a miss, {user.name}, but we're in soft mode "
            f"so the streak holds. Onto day {result.new_current_day} — tighten it up "
            f"today.")


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------
def schedule_jobs(app: Application) -> None:
    """Register the daily reminders + midnight rollover for both users."""
    if app.job_queue is None:
        log.warning("JobQueue unavailable — install python-telegram-bot[job-queue]. "
                    "Reminders are disabled.")
        return

    settings = load_settings()
    tz = ZoneInfo(settings.weather_timezone)
    jq = app.job_queue

    for user in USERS.values():
        if not user.telegram_user_id:
            log.warning("No Telegram id for %s — skipping their reminders.", user.slug)
            continue
        chat_id = user.telegram_user_id

        for reminder in DEFAULT_REMINDERS:
            at = reminder.at.replace(tzinfo=tz)
            jq.run_daily(
                _run_reminder,
                time=at,
                data={"slug": user.slug, "key": reminder.key},
                chat_id=chat_id,
                name=f"{user.slug}:{reminder.key}",
            )

        jq.run_daily(
            _run_rollover,
            time=dt_time(0, 5, tzinfo=tz),
            data={"slug": user.slug},
            chat_id=chat_id,
            name=f"{user.slug}:rollover",
        )

    log.info("Scheduled reminders for %d user(s).", len(USERS))


# ---------------------------------------------------------------------------
# /preview — fire a reminder on demand (for testing)
# ---------------------------------------------------------------------------
@restricted
async def preview(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/preview [key] — send a reminder now. key ∈ morning|midday|afternoon|
    late_afternoon|evening|night|final|rollover."""
    key = (context.args[0] if context.args else "morning").lower()
    valid = list(_BUILDERS) + ["rollover"]
    if key not in valid:
        await update.effective_message.reply_text(
            "Usage: /preview <key>\nKeys: " + ", ".join(valid)
        )
        return

    if key == "rollover":
        results = await asyncio.to_thread(db.finalize_due_days, user.slug)
        if not results:
            await update.effective_message.reply_text(
                "Nothing to roll over yet (no unfinalized past day)."
            )
            return
        for result in results:
            await update.effective_message.reply_text(_verdict_message(user, result))
        return

    state = await asyncio.to_thread(db.get_checklist_state, user)
    text = await _BUILDERS[key](user, state)
    await update.effective_message.reply_text(
        text or f"(Nothing to send for '{key}' — that task is already done.)"
    )
