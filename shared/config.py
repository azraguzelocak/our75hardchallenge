"""Central configuration for the 75 Hard tracker.

Everything that the bot and the dashboard both need to agree on lives here:
secrets (loaded from the environment), the two users, and — importantly —
each user's task set defined as *data* so the rest of the app never hardcodes
the checklist.

Edit the values in your ".env" file, not this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import time
from enum import Enum

from dotenv import load_dotenv

# Load ".env" once, when this module is first imported.
load_dotenv()


# ---------------------------------------------------------------------------
# Small helpers for reading the environment
# ---------------------------------------------------------------------------
def _env(key: str, default: str | None = None, required: bool = False) -> str | None:
    """Read an environment variable, optionally requiring it to be present."""
    value = os.getenv(key, default)
    if required and (value is None or value == ""):
        raise RuntimeError(
            f"Missing required environment variable '{key}'. "
            f"Copy .env.example to .env and fill it in."
        )
    return value


def _env_int(key: str, default: int | None = None) -> int | None:
    """Read an environment variable as an int (None if unset/blank)."""
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


# ---------------------------------------------------------------------------
# Challenge mode: strict 75 Hard vs a softer streak-keeping variant
# ---------------------------------------------------------------------------
class Mode(str, Enum):
    """How a missed task is handled.

    - strict: true 75 Hard — any miss resets the day counter to 1.
    - soft:   the miss is logged but the streak keeps going.
    """

    STRICT = "strict"
    SOFT = "soft"


# ---------------------------------------------------------------------------
# Task definitions — the checklist, as data
# ---------------------------------------------------------------------------
class TaskKind(str, Enum):
    """How a task is completed / measured."""

    BOOLEAN = "boolean"      # simply done / not done
    COUNTER = "counter"      # accumulate toward a target (e.g. water, pages)
    SESSIONS = "sessions"    # count sessions toward a target (e.g. 2 workouts)
    MAXIMUM = "maximum"      # passes while value stays at or below a cap (cigarettes)


@dataclass(frozen=True)
class TaskDef:
    """A single task in a user's daily checklist.

    `key` is the stable identifier stored in the database; `label` is the
    sentence-case text shown to the user. `target`/`unit` drive counter and
    session tasks; `cap` drives maximum tasks.

    `emoji`, `increments` and `presets` drive the tap-to-complete buttons so
    the bot's keyboard stays data-driven:
      * increments -> "+N" buttons that add to a running total (water, reading)
      * presets    -> buttons that *set* the value outright (e.g. sleep hours)
    """

    key: str
    label: str
    kind: TaskKind
    target: float | None = None      # goal for counter / sessions tasks
    cap: float | None = None         # allowed maximum for maximum tasks
    unit: str | None = None          # e.g. "ml", "pages", "hours", "cigarettes"
    requires_outdoor: bool = False   # workouts: at least one must be outdoors
    emoji: str = ""                  # shown on the buttons
    increments: tuple[float, ...] = ()  # "+N" add buttons
    presets: tuple[float, ...] = ()     # "set to N" buttons


# Tasks shared by both users (in display order).
_COMMON_TASKS: list[TaskDef] = [
    TaskDef("nutrition", "Follow the nutrition plan", TaskKind.BOOLEAN, emoji="🥗"),
    TaskDef("no_alcohol", "No alcohol", TaskKind.BOOLEAN, emoji="🚫"),
    TaskDef(
        "workouts",
        "Two 45-minute workouts (one outdoors)",
        TaskKind.SESSIONS,
        target=2,
        unit="sessions",
        requires_outdoor=True,
        emoji="🏋️",
    ),
    TaskDef(
        "water",
        "Drink 3.8 L of water",
        TaskKind.COUNTER,
        target=3800,
        unit="ml",
        emoji="💧",
        increments=(250, 500),
    ),
    TaskDef(
        "reading",
        "Read 10 pages",
        TaskKind.COUNTER,
        target=10,
        unit="pages",
        emoji="📖",
        increments=(5, 10),
    ),
    TaskDef("progress_photo", "Take a daily progress photo", TaskKind.BOOLEAN, emoji="📸"),
]

# The only difference between the two users is the final task.
_AZRA_EXTRA = TaskDef(
    "sleep",
    "Sleep at least 8 hours",
    TaskKind.COUNTER,
    target=8,
    unit="hours",
    emoji="😴",
    presets=(7, 8, 9),
)
_BERRIN_EXTRA = TaskDef(
    "cigarettes",
    "Maximum 2 cigarettes per day",
    TaskKind.MAXIMUM,
    cap=2,
    unit="cigarettes",
    emoji="🚬",
    increments=(1,),
)


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UserConfig:
    """Static, code-level config for one user.

    Per-user numbers that the users should set themselves (start weight, goal,
    nutrition targets, start date, reminder times) live in the database and the
    onboarding flow — not here. This holds only what the app needs to boot:
    the identity, the Telegram id allow-list entry, and the task set.
    """

    slug: str                 # internal id, e.g. "azra"
    name: str                 # display name, e.g. "Azra"
    telegram_user_id: int | None
    tasks: list[TaskDef] = field(default_factory=list)
    dashboard_pin: str | None = None

    def task(self, key: str) -> TaskDef | None:
        """Look up a task definition by key."""
        return next((t for t in self.tasks if t.key == key), None)


USERS: dict[str, UserConfig] = {
    "azra": UserConfig(
        slug="azra",
        name="Azra",
        telegram_user_id=_env_int("AZRA_TELEGRAM_ID"),
        tasks=[*_COMMON_TASKS, _AZRA_EXTRA],
        dashboard_pin=_env("AZRA_DASHBOARD_PIN"),
    ),
    "berrin": UserConfig(
        slug="berrin",
        name="Berrin",
        telegram_user_id=_env_int("BERRIN_TELEGRAM_ID"),
        tasks=[*_COMMON_TASKS, _BERRIN_EXTRA],
        dashboard_pin=_env("BERRIN_DASHBOARD_PIN"),
    ),
}


def user_by_telegram_id(telegram_user_id: int) -> UserConfig | None:
    """Return the user whose Telegram id matches, or None (the allow-list)."""
    for user in USERS.values():
        if user.telegram_user_id == telegram_user_id:
            return user
    return None


def the_other_user(slug: str) -> UserConfig:
    """Return the *other* of the two users — used for photo forwarding."""
    others = [u for s, u in USERS.items() if s != slug]
    if len(others) != 1:
        raise RuntimeError("This app is built for exactly two users.")
    return others[0]


def allowed_telegram_ids() -> set[int]:
    """The set of Telegram ids permitted to use the bot."""
    return {u.telegram_user_id for u in USERS.values() if u.telegram_user_id is not None}


# ---------------------------------------------------------------------------
# Reminder schedule — default times, overridable per user in the database
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ReminderTime:
    """A named daily reminder and its default time of day (local)."""

    key: str
    label: str
    at: time


DEFAULT_REMINDERS: list[ReminderTime] = [
    ReminderTime("morning", "Today's checklist + workout plan + weather", time(8, 0)),
    ReminderTime("midday", "Water check toward 3.8 L", time(12, 30)),
    ReminderTime("afternoon", "Water check toward 3.8 L", time(15, 30)),
    ReminderTime("late_afternoon", "Second workout done yet? One needs to be outdoors", time(17, 30)),
    ReminderTime("evening", "Progress photo + weight", time(19, 30)),
    ReminderTime("night", "Read your 10 pages + log sleep / cigarettes", time(21, 30)),
    ReminderTime("final", "Anything still incomplete fails the day", time(22, 45)),
]


# ---------------------------------------------------------------------------
# App-wide settings
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """Secrets and global settings, assembled from the environment."""

    # Telegram
    telegram_bot_token: str

    # Anthropic
    anthropic_api_key: str
    anthropic_model: str

    # Supabase
    supabase_url: str
    supabase_service_key: str
    supabase_db_url: str
    photo_bucket: str

    # Weather (Open-Meteo, no key needed)
    weather_latitude: float
    weather_longitude: float
    weather_timezone: str

    # Challenge default mode (each user can still override in their settings)
    default_mode: Mode = Mode.STRICT


def load_settings() -> Settings:
    """Build the Settings object, validating that required secrets exist."""
    return Settings(
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN", required=True),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", required=True),
        anthropic_model=_env("ANTHROPIC_MODEL", default="claude-sonnet-4-6"),
        supabase_url=_env("SUPABASE_URL", required=True),
        supabase_service_key=_env("SUPABASE_SERVICE_KEY", required=True),
        supabase_db_url=_env("SUPABASE_DB_URL", required=True),
        photo_bucket=_env("SUPABASE_PHOTO_BUCKET", default="photos"),
        weather_latitude=float(_env("WEATHER_LATITUDE", default="52.3676")),
        weather_longitude=float(_env("WEATHER_LONGITUDE", default="4.9041")),
        weather_timezone=_env("WEATHER_TIMEZONE", default="Europe/Amsterdam"),
        default_mode=Mode(_env("DEFAULT_MODE", default="strict")),
    )
