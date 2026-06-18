"""Rendering for the tap-to-complete checklist.

Pure presentation: turns a `ChecklistState` into the message text and the inline
keyboard. The buttons are derived from each task's definition (emoji /
increments / presets) so this stays data-driven — see shared/config.py.

Callback data format:  "t|<task_key>|<op>|<arg>"
  op = toggle            boolean task on/off
  op = add,   arg = N    add N to a counter / maximum task
  op = set,   arg = N    set a counter task's value to N
  op = wkt,   arg=in|out log an indoor / outdoor workout session
  op = refresh           just redraw
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from shared.config import TaskKind
from shared.db import ChecklistState, TaskState

PREFIX = "t"


def cb(task_key: str, op: str, arg: str = "") -> str:
    """Build callback data (kept well under Telegram's 64-byte limit)."""
    return f"{PREFIX}|{task_key}|{op}|{arg}"


def parse_cb(data: str) -> tuple[str, str, str]:
    """Parse callback data into (task_key, op, arg)."""
    _, task_key, op, arg = (data.split("|", 3) + ["", "", "", ""])[:4]
    return task_key, op, arg


# ---------------------------------------------------------------------------
# Message text
# ---------------------------------------------------------------------------
def build_text(state: ChecklistState, *, name: str) -> str:
    """Render the checklist as a message body."""
    day_label = (
        "warm-up (challenge not started yet)" if state.day_number == 0
        else f"day {state.day_number} of 75"
    )
    lines = [
        f"*{name} — {day_label}*",
        f"Today's checklist · {state.completed_count}/{state.total_count} done",
        "",
    ]
    for ts in state.tasks:
        mark = "✅" if ts.complete else "⬜"
        detail = f"  _{ts.detail}_" if _show_detail(ts) else ""
        lines.append(f"{mark} {ts.task.emoji} {ts.task.label}{detail}")

    lines.append("")
    if state.all_complete:
        lines.append("🎉 All tasks done — day complete! Keep it locked in.")
    else:
        remaining = state.total_count - state.completed_count
        lines.append(f"⏳ {remaining} task(s) still to go today.")
    return "\n".join(lines)


def _show_detail(ts: TaskState) -> bool:
    """Show the progress string for measured tasks, not plain booleans."""
    return ts.task.kind is not TaskKind.BOOLEAN


# ---------------------------------------------------------------------------
# Inline keyboard
# ---------------------------------------------------------------------------
def build_keyboard(state: ChecklistState) -> InlineKeyboardMarkup:
    """Build the tap-to-complete keyboard, one row of buttons per task."""
    rows: list[list[InlineKeyboardButton]] = []
    for ts in state.tasks:
        row = _task_row(ts)
        if row:
            rows.append(row)
    rows.append([InlineKeyboardButton("🔄 Refresh", callback_data=cb("_", "refresh"))])
    return InlineKeyboardMarkup(rows)


def _task_row(ts: TaskState) -> list[InlineKeyboardButton]:
    """The action buttons for one task, derived from its definition."""
    task = ts.task
    e = task.emoji

    if task.kind is TaskKind.BOOLEAN:
        mark = "✅" if ts.complete else "⬜"
        return [InlineKeyboardButton(f"{mark} {e} {_short(task.label)}",
                                     callback_data=cb(task.key, "toggle"))]

    if task.kind is TaskKind.SESSIONS:  # workouts
        return [
            InlineKeyboardButton(f"🏋️ +indoor", callback_data=cb(task.key, "wkt", "in")),
            InlineKeyboardButton(f"🌳 +outdoor", callback_data=cb(task.key, "wkt", "out")),
        ]

    buttons: list[InlineKeyboardButton] = []
    for inc in task.increments:
        buttons.append(
            InlineKeyboardButton(f"{e} +{_n(inc)}", callback_data=cb(task.key, "add", _n(inc)))
        )
    for preset in task.presets:
        unit = task.unit[0] if task.unit else ""
        buttons.append(
            InlineKeyboardButton(f"{e} {_n(preset)}{unit}", callback_data=cb(task.key, "set", _n(preset)))
        )
    # A reset for cumulative measured tasks (e.g. miscounted water / cigarettes).
    if task.increments:
        buttons.append(InlineKeyboardButton("↺ 0", callback_data=cb(task.key, "set", "0")))
    return buttons


def _short(label: str) -> str:
    """A compact button label (buttons are narrow)."""
    return label if len(label) <= 22 else label[:21] + "…"


def _n(number: float) -> str:
    """Whole numbers without a trailing ".0"."""
    return str(int(number)) if float(number).is_integer() else f"{number:g}"
