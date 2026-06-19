"""Level 2 coach tools — let the chat log data by calling these.

Every tool is hard-scoped to a user `slug` the app passes in — the model never
chooses whose data to touch. Only **additive** logging is allowed here;
destructive actions (delete, reset day/streak) are deliberately NOT tools — they
require explicit UI confirmation. All inputs are validated before any DB write.
Reuses the same shared.db functions the Telegram bot uses.
"""

from __future__ import annotations

from shared import db
from shared.config import USERS, TaskKind

# Tool schemas passed to the Anthropic API.
TOOL_SCHEMAS = [
    {
        "name": "log_task",
        "description": (
            "Mark a daily checklist task done, or add to a measured task "
            "(water in ml, reading in pages, sleep in hours, cigarettes count). "
            "Use log_workout for workouts. task_key must be one of the user's tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_key": {"type": "string",
                             "description": "e.g. nutrition, no_alcohol, water, reading, "
                                            "progress_photo, sleep, cigarettes"},
                "value": {"type": "number",
                          "description": "amount for measured tasks (ml/pages/hours/count); "
                                         "omit for simple done tasks"},
            },
            "required": ["task_key"],
        },
    },
    {
        "name": "add_weight",
        "description": "Log today's weight in kg and optional measurements in cm.",
        "input_schema": {
            "type": "object",
            "properties": {
                "weight": {"type": "number"}, "waist": {"type": "number"},
                "hips": {"type": "number"}, "arms": {"type": "number"},
            },
            "required": ["weight"],
        },
    },
    {
        "name": "log_meal",
        "description": "Log a meal eaten today with its nutrition (text only, no photo).",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"}, "calories": {"type": "number"},
                "protein": {"type": "number"}, "carbs": {"type": "number"},
                "fat": {"type": "number"},
            },
            "required": ["description", "calories"],
        },
    },
    {
        "name": "log_workout",
        "description": "Log a workout session done today.",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "duration_min": {"type": "number"},
                "is_outdoor": {"type": "boolean"},
                "calories_burned": {"type": "number"},
            },
            "required": ["description", "is_outdoor"],
        },
    },
    {
        "name": "log_reading",
        "description": "Log pages read today.",
        "input_schema": {
            "type": "object",
            "properties": {"pages": {"type": "number"}, "book_title": {"type": "string"}},
            "required": ["pages"],
        },
    },
    {
        "name": "get_streak",
        "description": "Get the user's current challenge day and streak.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_summary",
        "description": "Get a snapshot of the user's day (tasks, nutrition, workouts, weight).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_trends",
        "description": "Get this week vs last week (workouts, avg calories, pages/day, weight).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "remember",
        "description": ("Save a lasting preference or fact about the user (dietary "
                        "needs, injuries, foods they like, their goal) so future "
                        "advice is personalized."),
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string"}},
            "required": ["note"],
        },
    },
]


def _num(value, lo: float, hi: float, name: str) -> float:
    """Validate a number is within a sane range, else raise."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number")
    if not (lo <= f <= hi):
        raise ValueError(f"{name} {f} is out of range ({lo}–{hi})")
    return f


def run_tool(slug: str, name: str, args: dict) -> str:
    """Execute a tool for `slug`. Returns a short human-readable result string.

    `slug` is supplied by the app (the logged-in user) — never by the model.
    """
    user = USERS[slug]
    uid = db.get_user_id(slug)
    today = db.app_today()
    args = args or {}

    if name == "get_streak":
        row = db.get_user_row(slug)
        return f"Day {row['current_day']} of 75, current streak {row['current_streak']}."

    if name == "get_summary":
        from shared import coach
        return coach.build_context(slug)

    if name == "get_trends":
        from shared import coach
        return coach.week_summary(uid)

    if name == "remember":
        note = str(args.get("note", "")).strip()
        if not note:
            return "Tell me what you'd like me to remember."
        db.append_coach_note(uid, note[:500])
        return f"Got it — I'll remember that: {note}"

    if name == "add_weight":
        weight = _num(args["weight"], 20, 400, "weight")
        extra = {}
        for k in ("waist", "hips", "arms"):
            if args.get(k) is not None:
                extra[k] = _num(args[k], 10, 300, k)
        db.add_weight(uid, today, weight=weight, **extra)
        tail = (" " + ", ".join(f"{k} {v}cm" for k, v in extra.items())) if extra else ""
        return f"Logged weight {weight} kg.{tail}"

    if name == "log_meal":
        cal = int(_num(args["calories"], 0, 10000, "calories"))
        p = _num(args.get("protein") or 0, 0, 2000, "protein")
        c = _num(args.get("carbs") or 0, 0, 2000, "carbs")
        f = _num(args.get("fat") or 0, 0, 2000, "fat")
        desc = str(args["description"])[:200]
        db.add_meal(uid, today, description=desc, calories=cal, protein=p, carbs=c, fat=f)
        return f"Logged '{desc}' — {cal} kcal, {p:g}g protein."

    if name == "log_workout":
        dur = int(_num(args.get("duration_min") or 45, 1, 600, "duration_min"))
        cal = int(_num(args.get("calories_burned") or 0, 0, 10000, "calories_burned"))
        outdoor = bool(args.get("is_outdoor"))
        desc = str(args["description"])[:200]
        db.log_workout(uid, today, is_outdoor=outdoor, description=desc,
                       duration_min=dur, ai_calories_burned=cal)
        total, od = db.get_workout_counts(uid, today)
        where = "outdoor" if outdoor else "indoor"
        return f"Logged {where} workout ({dur} min). Today: {total}/2 ({od} outdoor)."

    if name == "log_reading":
        pages = int(_num(args["pages"], 1, 10000, "pages"))
        db.add_reading_log(uid, today, pages)
        state = db.get_checklist_state(user)
        comps = db.get_completions_map(state.log["id"])
        new_total = float((comps.get("reading") or {}).get("value") or 0) + pages
        db.upsert_completion(state.log["id"], "reading",
                             completed=new_total >= 10, value=new_total)
        return f"Logged {pages} pages. Reading today: {int(new_total)}/10."

    if name == "log_task":
        task_key = str(args.get("task_key", "")).strip().lower()
        task = user.task(task_key)
        if task is None:
            valid = ", ".join(t.key for t in user.tasks)
            return f"Error: '{task_key}' is not one of your tasks ({valid})."
        state = db.get_checklist_state(user)
        log_id = state.log["id"]
        if task.kind is TaskKind.BOOLEAN:
            db.upsert_completion(log_id, task_key, completed=True, value=None)
            return f"Marked '{task.label}' done."
        if task.kind is TaskKind.SESSIONS:
            return "For workouts, use log_workout instead."
        value = _num(args.get("value", task.target or 0), 0, 100000, "value")
        if task.kind is TaskKind.MAXIMUM:
            completed = value <= (task.cap or 0)
        else:
            completed = value >= (task.target or 0)
        db.upsert_completion(log_id, task_key, completed=completed, value=value)
        return f"Logged {task.label}: {value:g} {task.unit or ''}".strip() + "."

    return f"Error: unknown tool '{name}'."
