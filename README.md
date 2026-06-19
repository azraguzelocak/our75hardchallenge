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

### 7. Dashboard login passwords

The dashboard requires a login. Set each person's password as a **salted bcrypt
hash** (never plaintext):

```powershell
python -m scripts.set_password azra
python -m scripts.set_password berrin
```

Each prints a line like `AZRA_PASSWORD_HASH='$2b$12$...'` — paste it into `.env`
(local) and/or your Streamlit secrets (hosted). If no hash is set, the dashboard
runs open (handy on a first local run).

## Running

```powershell
# Bot — Telegram handlers + scheduled reminders
python -m bot.main

# Dashboard — binds to localhost only (see config). Log in with azra/berrin.
streamlit run dashboard/app.py
```

## Configurable settings (you set these — nothing is prescribed)

- **Mode**: `strict` (any miss resets to day 1) or `soft` (logs the miss, keeps
  the streak). Default `strict`; per-user override stored in the database.
- **Start weight / goal / height**, **nutrition targets**, **reminder times**,
  and **challenge start date** — per user, set during onboarding (phase 2+) or
  editable directly in the `users` table. All optional; you can just log from
  day 1.

## Coach chatbot (in the dashboard)

A floating **💬 Coach** widget (bottom-right, on every view) talks to a 75 Hard
coach that knows your real numbers and can log for you:

- **Data-aware:** before each reply it pulls the logged-in user's data (day,
  streak, today's tasks, calories/protein vs target, workouts this week, weight,
  current book) so answers are about *your* numbers.
- **Can log by chatting:** tools for `log_task`, `add_weight`, `log_meal`,
  `log_workout`, `log_reading`, `get_streak`, `get_summary` — reusing the same DB
  functions the Telegram bot uses. It asks a clarifying question when a request
  is ambiguous, and validates inputs before writing.
- **Model:** `claude-sonnet-4-6`, streamed replies.

## Security model

- **Login:** the whole dashboard is gated by a username + **bcrypt-hashed**
  password (`shared/auth.py`). Only hashes are stored, in `.env` / secrets —
  never plaintext, never in the repo.
- **Per-user scoping:** the coach only ever reads/writes the **logged-in user's**
  data — the user ID is injected by the app, never chosen by the model.
- **No destructive actions from chat:** the coach has only additive/read tools.
  Resetting the day/streak lives in **Settings → Danger zone** behind an explicit
  confirm checkbox + button.
- **Photos never sent to the API:** progress photos are display-only; only food
  photos (which need analysis) go to Anthropic, and they're kept in a separate
  table from progress photos. The chatbot sends no images at all.
- **Secrets server-side only:** the Anthropic key + DB credentials stay in
  `.env` / secrets; the chatbot is instructed never to reveal them.
- **Cost guard:** capped `max_tokens` per reply, ≤6 tool steps per turn, and a
  per-session message limit.
- The Telegram bot also rejects any Telegram id not on the allow-list.

## Running privately over Tailscale

The dashboard binds to **localhost only** (`.streamlit/config.toml`) — it is not
exposed to the LAN or the public internet. To reach it from your phone, use
[Tailscale](https://tailscale.com/) (no router ports, encrypted):

```powershell
# 1. Start the dashboard (localhost-bound)
streamlit run dashboard/app.py

# 2. Expose it to your tailnet only (TLS, private to your devices):
tailscale serve --bg 8520
#   then open the printed https://<machine>.<tailnet>.ts.net URL on your phone
```

Keep the bot running too (`python -m bot.main`) for logging + reminders. For
24/7 reminders, run both on an always-on machine.

> **Streamlit Community Cloud note:** if you instead host the dashboard on
> Streamlit Cloud, it is public by URL — there your privacy comes from the
> **login** above (set `*_PASSWORD_HASH` in the app's secrets), not from network
> binding. Don't put the bot token there; the dashboard only needs the Supabase
> keys + the password hashes.

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
