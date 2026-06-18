# 75 Hard tracker

A private daily-tracking app for a [75 Hard](https://andyfrisella.com/pages/75-hard-info)
challenge run by two people, Azra and Berrin. It has two surfaces sharing one
database:

1. **A Telegram bot** — the all-day driver: log tasks, send food and progress
   photos, get reminders, share progress photos with each other.
2. **A Streamlit dashboard** — the visual side: trends, charts, calendar
   heatmap, comparison, before/after slider, and the day-75 report.

> Build status: **phases 1–6 complete** (scaffold, bot core, AI logging,
> photos + sharing + weight, reminders + weather, dashboard). Phase 7 (polish +
> deploy) is next — see the build order at the bottom.

## Bot commands

| Command | What it does |
| --- | --- |
| `/start` | Register and show today's checklist |
| `/today` | Show today's tap-to-complete checklist |
| `/workout` | AI plan: one indoor + one outdoor, rotating muscle groups |
| `/logworkout <text>` | Log a workout from a description (AI estimates calories) |
| `/meals` | Quick-pick favorite meals to re-log in one tap |
| `/summary` | Short AI end-of-day recap |
| `/target <cal> [protein]` | Set your own nutrition targets |
| `/weight <kg> [waist hips arms]` | Log weight + optional measurements |
| `/preview <key>` | Fire a reminder now to test it (morning, midday, night, final, rollover, …) |

Send a **photo** any time → the bot asks if it's a **meal** (→ calories +
macros), a **workout** (→ calories burned + indoor/outdoor), or a **progress
photo** (→ saved, ticks the task, and forwarded to the other user).

## Dashboard

```powershell
streamlit run dashboard/app.py
```

Dark + red 75 Hard theme. Sidebar navigates nine views: **overview** (day
badge, streaks, 75-day completion grid, milestones), **trends** (weight with a
7-day trend line + goal/start markers, measurements), **calendar** (GitHub-style
heatmap — green = full day completed), **nutrition** (calories vs target +
macros + meal photos), **workouts** (sessions, indoor/outdoor, calories burned),
**reading** (book progress + pages/day), **compare** (both of you side by side),
**photos** (before/after slider, gated by a per-user PIN), and the **day-75
report** (exportable summary + before/after).

## How the pieces fit

- One shared bot serves both users; it identifies each by Telegram user id and
  rejects anyone not on the allow-list (the app is private to the two of us).
- The bot writes everything to **Supabase** (Postgres) and stores photos in a
  Supabase **Storage** bucket.
- Food photos and workout descriptions go to the **Anthropic API**
  (`claude-sonnet-4-6`, vision) for estimates, which are saved and replied back.
- The Streamlit dashboard reads the same Postgres database.

## Project structure

```
me/
├── .env.example            # template for secrets — copy to .env
├── requirements.txt
├── README.md
├── shared/                 # code shared by bot + dashboard
│   ├── config.py           # users, task sets (as data), settings, secrets
│   └── db.py               # Supabase client + data access layer
├── bot/
│   └── main.py             # bot entry point (handlers added in phase 2)
├── dashboard/
│   └── app.py              # Streamlit entry point (views added in phase 6)
├── scripts/
│   └── seed.py             # push users + task sets into Supabase
└── database/
    └── migrations/
        ├── 0001_init.sql   # database schema
        └── 0002_day_finalize.sql
```

The daily checklist is **defined as data** in `shared/config.py`
(`TaskDef` / `UserConfig`), so the rest of the app never hardcodes the tasks.
Azra and Berrin share the same six tasks; only the last differs (Azra: sleep
≥ 8 h, Berrin: ≤ 2 cigarettes).

## Setup

### 1. Python environment

Python 3.11+ recommended.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1      # Windows PowerShell
pip install -r requirements.txt
```

### 2. Telegram bot token (from BotFather)

1. In Telegram, open a chat with [@BotFather](https://t.me/BotFather).
2. Send `/newbot`, choose a name and a username ending in `bot`.
3. Copy the token it gives you into `TELEGRAM_BOT_TOKEN` in `.env`.
4. Find each person's numeric Telegram id by messaging
   [@userinfobot](https://t.me/userinfobot); put them in `AZRA_TELEGRAM_ID`
   and `BERRIN_TELEGRAM_ID`.

### 3. Anthropic API key

Create a key at <https://console.anthropic.com/> and set `ANTHROPIC_API_KEY`.

### 4. Supabase

1. Create a free project at <https://supabase.com/>.
2. In **SQL editor**, paste and run `database/migrations/0001_init.sql`.
3. In **Storage**, create a bucket (default name `photos`, keep it private).
4. From **Project settings → API**, copy the project URL and the
   **service-role** key into `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`.
5. From **Project settings → Database**, copy the connection string into
   `SUPABASE_DB_URL` (used by the dashboard).

### 5. Environment file

```powershell
Copy-Item .env.example .env
# then edit .env with the real values
```

### 6. Seed the users

```powershell
python -m scripts.seed
```

## Running

```powershell
# Bot (phase 1: prints a scaffold check; real handlers in phase 2)
python -m bot.main

# Dashboard
streamlit run dashboard/app.py
```

## Configurable settings (you set these — nothing is prescribed)

- **Mode**: `strict` (any miss resets to day 1) or `soft` (logs the miss, keeps
  the streak). Default `strict`; per-user override stored in the database.
- **Start weight / goal / height**, **nutrition targets**, **reminder times**,
  and **challenge start date** — per user, set during onboarding (phase 2+) or
  editable directly in the `users` table. All optional; you can just log from
  day 1.

## Privacy and security

- The bot rejects any Telegram id not on the allow-list.
- All secrets live in `.env` (gitignored). `.env.example` holds placeholders.
- Photos are stored as private objects; tables keep only the object path, never
  bytes or public URLs.
- The dashboard's before/after slider shows each user only their own photos by
  default, gated by a per-user PIN (phase 6).

## Hosting (covered fully in phase 7)

The bot is a long-running process; a small always-on host such as
[Railway](https://railway.app/), [Fly.io](https://fly.io/) or a cheap VPS works
well. The dashboard can run on [Streamlit Community Cloud](https://streamlit.io/cloud)
pointed at the same Supabase database. Deploy steps come in phase 7.

## Build order

1. **Scaffold** — structure, `.env.example`, schema, config. ✅
2. **Bot core** — allow-list, `/start`, `/today`, tap-to-complete checklist,
   day pass/fail + counter (strict/soft). ✅
3. **AI logging** — food photo → macros, workout → calories + outdoor flag,
   `/workout` suggestion, daily summary. ✅
4. **Photos + sharing** — progress photo upload + forward; weight logging. ✅
5. **Reminders** — full JobQueue schedule + weather-aware outdoor suggestion. ✅
6. **Dashboard** — all Streamlit views + day-75 report. ✅
7. **Polish + deploy** — hosting, end-to-end test.
```
