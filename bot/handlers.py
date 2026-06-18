"""Command and callback handlers for the bot core (phase 2)."""

from __future__ import annotations

import asyncio

from telegram import Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from bot.auth import restricted
from bot.checklist import build_keyboard, build_text, parse_cb
from shared import db
from shared.config import TaskKind, UserConfig


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
@restricted
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/start — greet the user and show today's checklist."""
    state = await asyncio.to_thread(db.get_checklist_state, user)
    intro = (
        f"Hi {user.name}! 💪 Welcome to your 75 Hard tracker.\n"
        f"You're on day {state.day_number}. Tap the buttons below to log "
        f"today's tasks. Send /today any time to pull this back up."
    )
    await update.effective_message.reply_text(intro)
    await _send_checklist(update, user, state)


@restricted
async def today(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/today — show today's checklist with tap-to-complete buttons."""
    state = await asyncio.to_thread(db.get_checklist_state, user)
    await _send_checklist(update, user, state)


# ---------------------------------------------------------------------------
# Button taps
# ---------------------------------------------------------------------------
@restricted
async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """Handle a tap on the checklist keyboard: apply the action, then redraw."""
    query = update.callback_query
    task_key, op, arg = parse_cb(query.data)

    if op == "refresh":
        await query.answer("Refreshed")
    else:
        toast = await asyncio.to_thread(_apply_action, user, task_key, op, arg)
        await query.answer(toast)

    # Redraw with fresh state.
    state = await asyncio.to_thread(db.get_checklist_state, user)
    try:
        await query.edit_message_text(
            text=build_text(state, name=user.name),
            reply_markup=build_keyboard(state),
            parse_mode=ParseMode.MARKDOWN,
        )
    except BadRequest as exc:
        # Editing to identical content raises "message is not modified" — ignore.
        if "not modified" not in str(exc).lower():
            raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _send_checklist(update: Update, user: UserConfig, state: db.ChecklistState) -> None:
    await update.effective_message.reply_text(
        text=build_text(state, name=user.name),
        reply_markup=build_keyboard(state),
        parse_mode=ParseMode.MARKDOWN,
    )


def _apply_action(user: UserConfig, task_key: str, op: str, arg: str) -> str:
    """Apply one checklist action to the database. Returns a short toast.

    Runs in a worker thread (all Supabase calls here are synchronous).
    """
    state = db.get_checklist_state(user)        # ensures today's log exists
    log = state.log
    task = user.task(task_key)

    if op == "wkt":  # workout session
        is_outdoor = arg == "out"
        db.log_workout(state.user_row["id"], db.app_today(), is_outdoor=is_outdoor)
        where = "outdoor" if is_outdoor else "indoor"
        return f"Logged an {where} workout 🏋️"

    if task is None:
        return "Unknown task"

    completions = db.get_completions_map(log["id"])
    current = float((completions.get(task_key) or {}).get("value") or 0)

    if op == "toggle":
        was_done = bool((completions.get(task_key) or {}).get("completed"))
        db.upsert_completion(log["id"], task_key, completed=not was_done, value=None)
        return "Marked done ✅" if not was_done else "Unmarked"

    if op == "add":
        new_value = current + float(arg)
    elif op == "set":
        new_value = float(arg)
    else:
        return ""

    completed = _counter_complete(task.kind, new_value, task.target, task.cap)
    db.upsert_completion(log["id"], task_key, completed=completed, value=new_value)
    unit = task.unit or ""
    return f"{task.label.split()[0]}: {_n(new_value)} {unit}".strip()


def _counter_complete(kind: TaskKind, value: float, target, cap) -> bool:
    """Completion rule for measured tasks."""
    if kind is TaskKind.MAXIMUM:
        return value <= (cap or 0)
    return value >= (target or 0)  # COUNTER


def _n(number: float) -> str:
    return str(int(number)) if float(number).is_integer() else f"{number:g}"


# ---------------------------------------------------------------------------
# Weight + measurements (phase 4)
# ---------------------------------------------------------------------------
_WEIGHT_USAGE = (
    "Log your weight (kg) and optionally measurements (cm):\n"
    "/weight 82.5\n"
    "/weight 82.5 waist=80 hips=95 arms=35\n"
    "/weight 82.5 80 95 35   (weight, then waist, hips, arms)"
)

_MEASURE_ORDER = ("waist", "hips", "arms")


def _to_num(token: str) -> float | None:
    """Parse a number, tolerating a trailing unit like 'kg' or 'cm'."""
    cleaned = token.lower().rstrip("kgcm").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_weight_args(args: list[str]) -> dict | None:
    """Parse `/weight` args into a dict. Returns None if no weight is given."""
    if not args:
        return None
    weight = _to_num(args[0])
    if weight is None:
        return None

    parsed: dict[str, float] = {"weight": weight}
    positional = iter(_MEASURE_ORDER)
    for token in args[1:]:
        if "=" in token:  # labelled: waist=80
            key, _, val = token.partition("=")
            key = key.lower().strip()
            num = _to_num(val)
            if key in _MEASURE_ORDER and num is not None:
                parsed[key] = num
        else:  # positional: next of waist, hips, arms
            num = _to_num(token)
            key = next(positional, None)
            if key and num is not None:
                parsed[key] = num
    return parsed


@restricted
async def weight(update: Update, context: ContextTypes.DEFAULT_TYPE, user: UserConfig) -> None:
    """/weight — log weight and optional waist/hips/arms."""
    parsed = _parse_weight_args(context.args)
    if parsed is None:
        await update.effective_message.reply_text(_WEIGHT_USAGE)
        return

    user_id = await asyncio.to_thread(db.get_user_id, user.slug)
    await asyncio.to_thread(db.add_weight, user_id, db.app_today(), **parsed)

    # Build a confirmation, including change-from-start if we have a start weight.
    parts = [f"weight {_n(parsed['weight'])} kg"]
    for key in _MEASURE_ORDER:
        if key in parsed:
            parts.append(f"{key} {_n(parsed[key])} cm")
    lines = ["⚖️ Logged: " + ", ".join(parts)]

    user_row = await asyncio.to_thread(db.get_user_row, user.slug)
    start = user_row.get("start_weight")
    if start:
        delta = parsed["weight"] - float(start)
        arrow = "▼" if delta < 0 else ("▲" if delta > 0 else "—")
        lines.append(f"{arrow} {_n(abs(round(delta, 1)))} kg from your start weight.")
    await update.effective_message.reply_text("\n".join(lines))
