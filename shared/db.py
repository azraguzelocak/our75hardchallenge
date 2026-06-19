"""Database layer — the single place that talks to Supabase.

Both the bot and the dashboard import from here so the data access logic is
not duplicated.

Phase 2 adds the heart of the app:
  * daily logs and per-task completions,
  * the checklist state (what is done / pending today, with progress),
  * the day pass/fail engine with strict/soft mode and the day counter.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from functools import lru_cache
from zoneinfo import ZoneInfo

from supabase import Client, create_client

from shared import config
from shared.config import (
    USERS,
    Mode,
    TaskDef,
    TaskKind,
    UserConfig,
    load_settings,
)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def get_client() -> Client:
    """Return a cached Supabase client authenticated with the service key.

    Reads the two Supabase secrets directly so the dashboard only needs those
    (not the bot token / AI key) when deployed.
    """
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_KEY "
            "(set them in .env locally, or in Streamlit secrets when deployed)."
        )
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Time — "today" in the app's configured timezone
# ---------------------------------------------------------------------------
def app_today() -> dt.date:
    """Return today's date in the configured (local) timezone.

    Reads WEATHER_TIMEZONE directly (default Europe/Amsterdam) so dashboard
    paths don't require the bot's secrets via load_settings()."""
    tz = ZoneInfo(os.getenv("WEATHER_TIMEZONE") or "Europe/Amsterdam")
    return dt.datetime.now(tz).date()


def _iso(day: dt.date) -> str:
    return day.isoformat()


# ---------------------------------------------------------------------------
# Seeding — push the config's users + task sets into the database
# ---------------------------------------------------------------------------
def sync_users_and_tasks() -> dict[str, str]:
    """Insert or update the two users and their tasks from config.

    Returns a mapping of user slug -> database user id. Safe to run repeatedly.
    """
    client = get_client()
    slug_to_id: dict[str, str] = {}

    for user in USERS.values():
        row = {
            "slug": user.slug,
            "name": user.name,
            "telegram_user_id": user.telegram_user_id,
        }
        result = (
            client.table("users")
            .upsert(row, on_conflict="slug", ignore_duplicates=False)
            .execute()
        )
        user_id = result.data[0]["id"]
        slug_to_id[user.slug] = user_id
        _sync_tasks_for_user(client, user, user_id)

    return slug_to_id


def _sync_tasks_for_user(client: Client, user: UserConfig, user_id: str) -> None:
    """Upsert a single user's task set, preserving any `enabled` toggles."""
    rows = [
        {
            "user_id": user_id,
            "task_key": task.key,
            "label": task.label,
            "sort_order": index,
        }
        for index, task in enumerate(user.tasks)
    ]
    client.table("tasks").upsert(rows, on_conflict="user_id,task_key").execute()


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
def get_user_row(slug: str) -> dict | None:
    """Fetch a user's database row by slug."""
    result = get_client().table("users").select("*").eq("slug", slug).limit(1).execute()
    return result.data[0] if result.data else None


def get_user_row_by_id(user_id: str) -> dict | None:
    """Fetch a user's database row by id."""
    result = get_client().table("users").select("*").eq("id", user_id).limit(1).execute()
    return result.data[0] if result.data else None


def get_user_id(slug: str) -> str | None:
    """Fetch just a user's database id by slug."""
    row = get_user_row(slug)
    return row["id"] if row else None


def user_mode(user_row: dict) -> Mode:
    """The challenge mode for this user (falls back to the global default)."""
    raw = user_row.get("mode") or os.getenv("DEFAULT_MODE") or "strict"
    return Mode(raw)


def _challenge_start(user_row: dict) -> dt.date | None:
    """The user's challenge start date, if set."""
    raw = user_row.get("challenge_start_date")
    if not raw:
        return None
    return dt.date.fromisoformat(raw) if isinstance(raw, str) else raw


def challenge_started(user_row: dict, on: dt.date | None = None) -> bool:
    """True if the challenge has started by the given date (default today)."""
    start = _challenge_start(user_row)
    if start is None:
        return True  # no start date set → counts from day 1 immediately
    return (on or app_today()) >= start


# ---------------------------------------------------------------------------
# Daily logs + task completions
# ---------------------------------------------------------------------------
def get_daily_log(user_id: str, day: dt.date) -> dict | None:
    """Fetch a user's daily log for a given date, if it exists."""
    result = (
        get_client()
        .table("daily_logs")
        .select("*")
        .eq("user_id", user_id)
        .eq("date", _iso(day))
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


def _create_daily_log(user_id: str, day: dt.date, day_number: int) -> dict:
    result = (
        get_client()
        .table("daily_logs")
        .insert(
            {
                "user_id": user_id,
                "date": _iso(day),
                "day_number": day_number,
            }
        )
        .execute()
    )
    return result.data[0]


def get_completions_map(daily_log_id: str) -> dict[str, dict]:
    """Return {task_key: completion row} for a daily log."""
    result = (
        get_client()
        .table("task_completions")
        .select("*")
        .eq("daily_log_id", daily_log_id)
        .execute()
    )
    return {row["task_key"]: row for row in result.data}


def upsert_completion(
    daily_log_id: str, task_key: str, *, completed: bool, value: float | None
) -> dict:
    """Insert or update a single task completion."""
    result = (
        get_client()
        .table("task_completions")
        .upsert(
            {
                "daily_log_id": daily_log_id,
                "task_key": task_key,
                "completed": completed,
                "value": value,
            },
            on_conflict="daily_log_id,task_key",
        )
        .execute()
    )
    return result.data[0]


# ---------------------------------------------------------------------------
# Workouts (logged as rows so we can check the "1 outdoors" rule)
# ---------------------------------------------------------------------------
def log_workout(
    user_id: str,
    day: dt.date,
    *,
    is_outdoor: bool,
    description: str | None = None,
    duration_min: int | None = None,
    ai_calories_burned: int | None = None,
) -> dict:
    """Insert a workout session for the day."""
    result = (
        get_client()
        .table("workouts")
        .insert(
            {
                "user_id": user_id,
                "date": _iso(day),
                "is_outdoor": is_outdoor,
                "description": description,
                "duration_min": duration_min,
                "ai_calories_burned": ai_calories_burned,
            }
        )
        .execute()
    )
    return result.data[0]


def get_workout_counts(user_id: str, day: dt.date) -> tuple[int, int]:
    """Return (total sessions, outdoor sessions) logged on a given day."""
    result = (
        get_client()
        .table("workouts")
        .select("is_outdoor")
        .eq("user_id", user_id)
        .eq("date", _iso(day))
        .execute()
    )
    total = len(result.data)
    outdoor = sum(1 for r in result.data if r["is_outdoor"])
    return total, outdoor


# ---------------------------------------------------------------------------
# Checklist state — what the bot renders
# ---------------------------------------------------------------------------
@dataclass
class TaskState:
    """The live state of one task for one day."""

    task: TaskDef
    complete: bool
    value: float | None
    detail: str  # short human progress string, e.g. "1500 / 3800 ml"


@dataclass
class ChecklistState:
    """Everything the bot needs to render the checklist for today."""

    user_row: dict
    log: dict
    day_number: int
    tasks: list[TaskState]

    @property
    def completed_count(self) -> int:
        return sum(1 for t in self.tasks if t.complete)

    @property
    def total_count(self) -> int:
        return len(self.tasks)

    @property
    def all_complete(self) -> bool:
        return self.completed_count == self.total_count


def _evaluate_task(
    task: TaskDef, completion: dict | None, workout_counts: tuple[int, int]
) -> TaskState:
    """Decide whether a single task is complete and build its progress string."""
    value = completion["value"] if completion else None

    if task.kind is TaskKind.BOOLEAN:
        complete = bool(completion and completion["completed"])
        detail = "done" if complete else "not yet"

    elif task.kind is TaskKind.COUNTER:
        current = float(value or 0)
        complete = current >= (task.target or 0)
        target = _fmt(task.target)
        detail = f"{_fmt(current)} / {target} {task.unit or ''}".strip()

    elif task.kind is TaskKind.MAXIMUM:
        # Passes by default and keeps passing while at or below the cap.
        current = float(value or 0)
        complete = current <= (task.cap or 0)
        detail = f"{_fmt(current)} / {_fmt(task.cap)} {task.unit or ''}".strip()

    elif task.kind is TaskKind.SESSIONS:
        total, outdoor = workout_counts
        meets_count = total >= (task.target or 0)
        meets_outdoor = outdoor >= 1 if task.requires_outdoor else True
        complete = meets_count and meets_outdoor
        detail = f"{total} / {_fmt(task.target)} sessions ({outdoor} outdoor)"
        value = total

    else:  # pragma: no cover - defensive
        complete = False
        detail = ""

    return TaskState(task=task, complete=complete, value=value, detail=detail)


def _fmt(number: float | int | None) -> str:
    """Format a number without a trailing ".0" for whole values."""
    if number is None:
        return "0"
    if float(number).is_integer():
        return str(int(number))
    return f"{number:g}"


def get_checklist_state(user_config: UserConfig) -> ChecklistState:
    """Roll over any past days, ensure today's log exists, and read its state."""
    user_row = get_user_row(user_config.slug)
    if user_row is None:
        raise RuntimeError(
            f"User '{user_config.slug}' is not seeded. Run: python -m scripts.seed"
        )

    user_row = _ensure_today(user_row)
    log = get_daily_log(user_row["id"], app_today())
    completions = get_completions_map(log["id"])
    workout_counts = get_workout_counts(user_row["id"], app_today())

    tasks = [
        _evaluate_task(task, completions.get(task.key), workout_counts)
        for task in user_config.tasks
    ]

    state = ChecklistState(
        user_row=user_row,
        log=log,
        day_number=log["day_number"],
        tasks=tasks,
    )

    # Keep the live "day_passed" flag in sync (informational; the counter is
    # only moved at finalize time).
    if log.get("day_passed") != state.all_complete:
        get_client().table("daily_logs").update(
            {"day_passed": state.all_complete}
        ).eq("id", log["id"]).execute()
        state.log["day_passed"] = state.all_complete

    return state


# ---------------------------------------------------------------------------
# The day pass/fail engine
# ---------------------------------------------------------------------------
@dataclass
class FinalizeResult:
    """Outcome of finalizing a single day."""

    day_number: int
    passed: bool
    was_reset: bool       # strict mode reset the counter to day 1
    new_current_day: int
    new_streak: int


def _is_log_complete(user_config: UserConfig, user_id: str, log: dict) -> bool:
    """Are all of a user's enabled tasks complete for the given log?"""
    completions = get_completions_map(log["id"])
    day = dt.date.fromisoformat(log["date"])
    workout_counts = get_workout_counts(user_id, day)
    for task in user_config.tasks:
        state = _evaluate_task(task, completions.get(task.key), workout_counts)
        if not state.complete:
            return False
    return True


def _finalize_log(user_config: UserConfig, log: dict) -> FinalizeResult:
    """Apply pass/fail for one finished day and move the counter once."""
    user_row = get_user_row_by_id(log["user_id"])
    mode = user_mode(user_row)

    # Warm-up: days before the challenge start date don't count toward anything.
    log_date = dt.date.fromisoformat(log["date"])
    start = _challenge_start(user_row)
    if start and log_date < start:
        get_client().table("daily_logs").update({"finalized": True}).eq(
            "id", log["id"]
        ).execute()
        return FinalizeResult(
            day_number=0, passed=False, was_reset=False,
            new_current_day=user_row["current_day"],
            new_streak=user_row["current_streak"],
        )

    passed = _is_log_complete(user_config, user_row["id"], log)

    current_day = user_row["current_day"]
    current_streak = user_row["current_streak"]
    longest_streak = user_row["longest_streak"]
    was_reset = False

    if passed:
        current_day += 1
        current_streak += 1
        longest_streak = max(longest_streak, current_streak)
    else:
        if mode is Mode.STRICT:
            current_day = 1
            current_streak = 0
            was_reset = True
        else:  # soft: keep the streak going, just record the miss
            current_day += 1

    # Persist the day verdict and the user's counters.
    get_client().table("daily_logs").update(
        {"day_passed": passed, "finalized": True}
    ).eq("id", log["id"]).execute()

    get_client().table("users").update(
        {
            "current_day": current_day,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
        }
    ).eq("id", user_row["id"]).execute()

    return FinalizeResult(
        day_number=log.get("day_number") or 0,
        passed=passed,
        was_reset=was_reset,
        new_current_day=current_day,
        new_streak=current_streak,
    )


def _ensure_today(user_row: dict) -> dict:
    """Finalize any unfinalized past days, then ensure today's log exists.

    This "lazy roll-over on next interaction" means the day counter advances
    correctly without a scheduler. Phase 5 calls `finalize_due_days` from the
    JobQueue at midnight so days are also closed when nobody interacts.
    """
    user_config = USERS[user_row["slug"]]
    today = app_today()

    past = (
        get_client()
        .table("daily_logs")
        .select("*")
        .eq("user_id", user_row["id"])
        .lt("date", _iso(today))
        .eq("finalized", False)
        .order("date")
        .execute()
    )
    for log in past.data:
        _finalize_log(user_config, log)

    # Re-read the (possibly updated) user row before creating today's log.
    user_row = get_user_row_by_id(user_row["id"])

    log = get_daily_log(user_row["id"], today)
    if log is None:
        # Before the start date, today is a warm-up (day_number 0).
        day_no = user_row["current_day"] if challenge_started(user_row, today) else 0
        _create_daily_log(user_row["id"], today, day_no)
        user_row = get_user_row_by_id(user_row["id"])
    return user_row


def finalize_due_days(slug: str) -> list[FinalizeResult]:
    """Finalize every unfinalized day strictly before today, for one user.

    Returns one result per day closed (used by the night reminder in phase 5
    to send a supportive message on a reset). Safe to call any time.
    """
    user_config = USERS[slug]
    user_row = get_user_row(slug)
    if user_row is None:
        return []

    today = app_today()
    past = (
        get_client()
        .table("daily_logs")
        .select("*")
        .eq("user_id", user_row["id"])
        .lt("date", _iso(today))
        .eq("finalized", False)
        .order("date")
        .execute()
    )
    return [_finalize_log(user_config, log) for log in past.data]


# ---------------------------------------------------------------------------
# Meals (phase 3 — AI food logging)
# ---------------------------------------------------------------------------
def add_meal(user_id: str, day: dt.date, *, description: str, calories: int,
             protein: float, carbs: float, fat: float,
             photo_path: str | None = None, is_favorite: bool = False) -> dict:
    """Insert a meal with its AI nutrition estimate."""
    result = (
        get_client()
        .table("meals")
        .insert(
            {
                "user_id": user_id,
                "date": _iso(day),
                "description": description,
                "ai_calories": calories,
                "ai_protein": protein,
                "ai_carbs": carbs,
                "ai_fat": fat,
                "photo_path": photo_path,
                "is_favorite": is_favorite,
            }
        )
        .execute()
    )
    return result.data[0]


def get_meal(meal_id: str) -> dict | None:
    result = get_client().table("meals").select("*").eq("id", meal_id).limit(1).execute()
    return result.data[0] if result.data else None


def set_meal_favorite(meal_id: str, is_favorite: bool) -> None:
    get_client().table("meals").update({"is_favorite": is_favorite}).eq("id", meal_id).execute()


def get_favorite_meals(user_id: str, limit: int = 10) -> list[dict]:
    result = (
        get_client()
        .table("meals")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_favorite", True)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    # De-duplicate by description so the quick-pick isn't cluttered with repeats.
    seen: set[str] = set()
    unique: list[dict] = []
    for meal in result.data:
        key = (meal.get("description") or "").lower()
        if key not in seen:
            seen.add(key)
            unique.append(meal)
    return unique


def day_nutrition_totals(user_id: str, day: dt.date) -> dict:
    """Sum today's calories + macros across all logged meals."""
    result = (
        get_client()
        .table("meals")
        .select("ai_calories, ai_protein, ai_carbs, ai_fat")
        .eq("user_id", user_id)
        .eq("date", _iso(day))
        .execute()
    )
    totals = {"calories": 0, "protein": 0.0, "carbs": 0.0, "fat": 0.0}
    for m in result.data:
        totals["calories"] += m.get("ai_calories") or 0
        totals["protein"] += float(m.get("ai_protein") or 0)
        totals["carbs"] += float(m.get("ai_carbs") or 0)
        totals["fat"] += float(m.get("ai_fat") or 0)
    return totals


# ---------------------------------------------------------------------------
# Workouts — recent history (for muscle-group rotation in /workout)
# ---------------------------------------------------------------------------
def get_recent_workouts(user_id: str, days: int = 3) -> list[dict]:
    since = app_today() - dt.timedelta(days=days)
    result = (
        get_client()
        .table("workouts")
        .select("date, description, is_outdoor")
        .eq("user_id", user_id)
        .gte("date", _iso(since))
        .order("date", desc=True)
        .execute()
    )
    return result.data


# ---------------------------------------------------------------------------
# Progress photos (phase 4) + completing a task from outside the checklist
# ---------------------------------------------------------------------------
def add_progress_photo(user_id: str, day: dt.date, photo_path: str | None) -> dict:
    """Record a daily progress photo (one row per upload)."""
    result = (
        get_client()
        .table("progress_photos")
        .insert({"user_id": user_id, "date": _iso(day), "photo_path": photo_path or ""})
        .execute()
    )
    return result.data[0]


def complete_task(user_config: UserConfig, task_key: str, value: float | None = None) -> dict:
    """Mark a task complete for today (used when an action implies completion,
    e.g. uploading a progress photo completes the progress-photo task)."""
    state = get_checklist_state(user_config)
    return upsert_completion(state.log["id"], task_key, completed=True, value=value)


# ---------------------------------------------------------------------------
# Weight + measurements (phase 4)
# ---------------------------------------------------------------------------
def add_weight(user_id: str, day: dt.date, *, weight: float | None = None,
               waist: float | None = None, hips: float | None = None,
               arms: float | None = None, notes: str | None = None) -> dict:
    """Insert or update today's weight + measurements (one row per day)."""
    row = {"user_id": user_id, "date": _iso(day)}
    for key, val in (("weight", weight), ("waist", waist), ("hips", hips),
                     ("arms", arms), ("notes", notes)):
        if val is not None:
            row[key] = val
    result = (
        get_client()
        .table("weights")
        .upsert(row, on_conflict="user_id,date")
        .execute()
    )
    return result.data[0]


def add_reading_log(user_id: str, day: dt.date, pages: int,
                    book_id: str | None = None) -> dict:
    """Record pages read on a given day."""
    result = (
        get_client()
        .table("reading_logs")
        .insert({"user_id": user_id, "date": _iso(day), "pages_read": pages,
                 "book_id": book_id})
        .execute()
    )
    return result.data[0]


def latest_weight(user_id: str) -> dict | None:
    """The most recent weight entry (for the leaderboard / comparison)."""
    result = (
        get_client()
        .table("weights")
        .select("*")
        .eq("user_id", user_id)
        .not_.is_("weight", "null")
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    return result.data[0] if result.data else None


# ---------------------------------------------------------------------------
# Books (phase 6 — add + mark finished from the dashboard)
# ---------------------------------------------------------------------------
def add_book(user_id: str, title: str, total_pages: int | None = None) -> dict:
    result = (
        get_client()
        .table("books")
        .insert({"user_id": user_id, "title": title, "total_pages": total_pages})
        .execute()
    )
    return result.data[0]


def get_books(user_id: str) -> list[dict]:
    result = (
        get_client().table("books").select("*").eq("user_id", user_id)
        .order("created_at").execute()
    )
    return result.data


def get_weights(user_id: str) -> list[dict]:
    result = (
        get_client().table("weights").select("date, weight").eq("user_id", user_id)
        .order("date").execute()
    )
    return result.data


def get_reading_rows(user_id: str, days: int = 14) -> list[dict]:
    since = app_today() - dt.timedelta(days=days)
    result = (
        get_client().table("reading_logs").select("date, pages_read")
        .eq("user_id", user_id).gte("date", _iso(since)).execute()
    )
    return result.data


def get_meal_rows(user_id: str, days: int = 14) -> list[dict]:
    since = app_today() - dt.timedelta(days=days)
    result = (
        get_client().table("meals").select("date, ai_calories")
        .eq("user_id", user_id).gte("date", _iso(since)).execute()
    )
    return result.data


# ---------------------------------------------------------------------------
# Coach memory — preferences + persistent chat history (migration 0003)
# ---------------------------------------------------------------------------
def get_coach_profile(user_id: str) -> str:
    """The user's freeform coach preferences/notes (or '')."""
    try:
        result = (
            get_client().table("coach_profile").select("notes")
            .eq("user_id", user_id).limit(1).execute()
        )
        return result.data[0]["notes"] if result.data else ""
    except Exception:  # noqa: BLE001 - table may not exist yet
        return ""


def append_coach_note(user_id: str, note: str) -> None:
    """Append a remembered preference line to the user's coach profile."""
    existing = get_coach_profile(user_id)
    note = note.strip()
    combined = (existing + "\n- " + note).strip() if existing else "- " + note
    get_client().table("coach_profile").upsert(
        {"user_id": user_id, "notes": combined, "updated_at": "now()"},
        on_conflict="user_id",
    ).execute()


def add_coach_message(user_id: str, role: str, content: str) -> None:
    get_client().table("coach_messages").insert(
        {"user_id": user_id, "role": role, "content": content[:8000]}
    ).execute()


def get_coach_messages(user_id: str, limit: int = 20) -> list[dict]:
    """Most recent chat messages, oldest-first, as [{role, content}]."""
    try:
        result = (
            get_client().table("coach_messages").select("role, content")
            .eq("user_id", user_id).order("created_at", desc=True)
            .limit(limit).execute()
        )
    except Exception:  # noqa: BLE001 - table may not exist yet
        return []
    return [{"role": r["role"], "content": r["content"]} for r in reversed(result.data)]


def set_book_finished(book_id: str, finished: bool = True) -> None:
    """Mark a book finished (and snap current_page to total when finishing)."""
    updates: dict = {"is_finished": finished}
    if finished:
        book = (
            get_client().table("books").select("total_pages").eq("id", book_id)
            .limit(1).execute()
        )
        if book.data and book.data[0].get("total_pages"):
            updates["current_page"] = book.data[0]["total_pages"]
    get_client().table("books").update(updates).eq("id", book_id).execute()


# ---------------------------------------------------------------------------
# User settings — nutrition targets (set by the users themselves)
# ---------------------------------------------------------------------------
def reset_challenge(slug: str) -> None:
    """Destructive: reset a user's day counter and current streak to the start.

    Scoped to one slug. The longest-streak record is preserved. This is only
    ever called from an explicit, confirmed UI action — never from the chatbot.
    """
    get_client().table("users").update(
        {"current_day": 1, "current_streak": 0}
    ).eq("slug", slug).execute()


def set_targets(slug: str, *, calorie_target: int | None = None,
                protein_target: int | None = None) -> None:
    updates: dict = {}
    if calorie_target is not None:
        updates["daily_calorie_target"] = calorie_target
    if protein_target is not None:
        updates["protein_target"] = protein_target
    if updates:
        get_client().table("users").update(updates).eq("slug", slug).execute()


__all__ = [
    "config",
    "get_client",
    "app_today",
    "sync_users_and_tasks",
    "get_user_row",
    "get_user_row_by_id",
    "get_user_id",
    "user_mode",
    "get_daily_log",
    "get_completions_map",
    "upsert_completion",
    "log_workout",
    "get_workout_counts",
    "TaskState",
    "ChecklistState",
    "get_checklist_state",
    "FinalizeResult",
    "finalize_due_days",
    "add_meal",
    "get_meal",
    "set_meal_favorite",
    "get_favorite_meals",
    "day_nutrition_totals",
    "get_recent_workouts",
    "set_targets",
    "add_progress_photo",
    "complete_task",
    "add_weight",
    "latest_weight",
    "add_reading_log",
    "add_book",
    "get_books",
    "get_weights",
    "get_reading_rows",
    "get_meal_rows",
    "get_coach_profile",
    "append_coach_note",
    "add_coach_message",
    "get_coach_messages",
    "set_book_finished",
    "reset_challenge",
]
