-- ===========================================================================
-- 75 Hard tracker — initial schema
-- Run this in the Supabase SQL editor (or via the Supabase CLI) once, against
-- a fresh project. It is idempotent enough to re-run during development.
--
-- Notes:
--   * The bot connects with the service-role key and bypasses row level
--     security, so RLS is intentionally left off here — the app is private to
--     two allow-listed Telegram users and access is gated in code.
--   * Photos themselves live in a Storage bucket; these tables only store the
--     object paths (never the bytes, never a public URL).
-- ===========================================================================

-- Helper: keep an "updated_at" column fresh on update.
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;


-- ---------------------------------------------------------------------------
-- users — one row per person (Azra, Berrin)
-- ---------------------------------------------------------------------------
create table if not exists users (
    id                    uuid primary key default gen_random_uuid(),
    slug                  text unique not null,          -- "azra" / "berrin"
    telegram_user_id      bigint unique,                 -- allow-list lives in env; mirrored here
    name                  text not null,

    -- Per-user settings the users set themselves (nullable = "skip / log from day 1")
    start_weight          numeric,
    goal_weight           numeric,
    height                numeric,                       -- cm
    daily_calorie_target  integer,
    protein_target        integer,                       -- grams
    challenge_start_date  date,
    mode                  text not null default 'strict' -- 'strict' | 'soft'
                          check (mode in ('strict', 'soft')),

    -- Progress counters (maintained by the bot)
    current_day           integer not null default 1,
    current_streak        integer not null default 0,
    longest_streak        integer not null default 0,

    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);

drop trigger if exists trg_users_updated_at on users;
create trigger trg_users_updated_at before update on users
    for each row execute function set_updated_at();


-- ---------------------------------------------------------------------------
-- tasks — the per-user task set (mirrors shared/config.py task definitions)
-- ---------------------------------------------------------------------------
create table if not exists tasks (
    id        uuid primary key default gen_random_uuid(),
    user_id   uuid not null references users(id) on delete cascade,
    task_key  text not null,                 -- e.g. "water", "sleep", "cigarettes"
    label     text not null,
    enabled   boolean not null default true,
    sort_order integer not null default 0,
    unique (user_id, task_key)
);


-- ---------------------------------------------------------------------------
-- daily_logs — one row per user per day; the pass/fail verdict for that day
-- ---------------------------------------------------------------------------
create table if not exists daily_logs (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references users(id) on delete cascade,
    date         date not null,
    day_number   integer,                    -- which challenge day this was
    day_passed   boolean not null default false,
    notes        text,                       -- free-text journal
    mood         smallint,                   -- optional 1-5
    energy       smallint,                   -- optional 1-5
    created_at   timestamptz not null default now(),
    updated_at   timestamptz not null default now(),
    unique (user_id, date)
);

drop trigger if exists trg_daily_logs_updated_at on daily_logs;
create trigger trg_daily_logs_updated_at before update on daily_logs
    for each row execute function set_updated_at();


-- ---------------------------------------------------------------------------
-- task_completions — per-task state for a given daily_log
-- ---------------------------------------------------------------------------
create table if not exists task_completions (
    id            uuid primary key default gen_random_uuid(),
    daily_log_id  uuid not null references daily_logs(id) on delete cascade,
    task_key      text not null,
    completed     boolean not null default false,
    -- generic numeric value: water ml, pages read, sleep hours, cigarette count,
    -- workout sessions completed, etc.
    value         numeric,
    updated_at    timestamptz not null default now(),
    unique (daily_log_id, task_key)
);

drop trigger if exists trg_task_completions_updated_at on task_completions;
create trigger trg_task_completions_updated_at before update on task_completions
    for each row execute function set_updated_at();


-- ---------------------------------------------------------------------------
-- meals — food photos + AI nutrition estimates
-- ---------------------------------------------------------------------------
create table if not exists meals (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references users(id) on delete cascade,
    date         date not null,
    photo_path   text,                       -- object path in the storage bucket
    description  text,
    ai_calories  integer,
    ai_protein   numeric,                    -- grams
    ai_carbs     numeric,                    -- grams
    ai_fat       numeric,                    -- grams
    is_favorite  boolean not null default false,
    created_at   timestamptz not null default now()
);
create index if not exists idx_meals_user_date on meals (user_id, date);


-- ---------------------------------------------------------------------------
-- workouts — sessions, duration, outdoor flag, AI calories burned
-- ---------------------------------------------------------------------------
create table if not exists workouts (
    id                 uuid primary key default gen_random_uuid(),
    user_id            uuid not null references users(id) on delete cascade,
    date               date not null,
    description        text,
    duration_min       integer,
    is_outdoor         boolean not null default false,
    ai_calories_burned integer,
    photo_path         text,
    created_at         timestamptz not null default now()
);
create index if not exists idx_workouts_user_date on workouts (user_id, date);


-- ---------------------------------------------------------------------------
-- weights — scale weight + measurements over time
-- ---------------------------------------------------------------------------
create table if not exists weights (
    id        uuid primary key default gen_random_uuid(),
    user_id   uuid not null references users(id) on delete cascade,
    date      date not null,
    weight    numeric,
    waist     numeric,
    hips      numeric,
    arms      numeric,
    notes     text,
    created_at timestamptz not null default now(),
    unique (user_id, date)
);


-- ---------------------------------------------------------------------------
-- progress_photos — the daily "tagged as progress" photo
-- ---------------------------------------------------------------------------
create table if not exists progress_photos (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    date       date not null,
    photo_path text not null,
    created_at timestamptz not null default now()
);
create index if not exists idx_progress_photos_user_date on progress_photos (user_id, date);


-- ---------------------------------------------------------------------------
-- books + reading_logs — reading progress
-- ---------------------------------------------------------------------------
create table if not exists books (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references users(id) on delete cascade,
    title        text not null,
    total_pages  integer,
    current_page integer not null default 0,
    is_finished  boolean not null default false,
    created_at   timestamptz not null default now()
);

create table if not exists reading_logs (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    date       date not null,
    book_id    uuid references books(id) on delete set null,
    pages_read integer not null default 0,
    created_at timestamptz not null default now()
);
create index if not exists idx_reading_logs_user_date on reading_logs (user_id, date);
