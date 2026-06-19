-- ===========================================================================
-- 75 Hard tracker — migration 0003
-- Coach memory: per-user preferences + persistent chat history.
-- Run this in the Supabase SQL editor after 0001 and 0002.
-- ===========================================================================

-- Freeform preferences the coach remembers (dietary restrictions, injuries,
-- favourite foods, the user's "why", etc.). One row per user.
create table if not exists coach_profile (
    user_id    uuid primary key references users(id) on delete cascade,
    notes      text not null default '',
    updated_at timestamptz not null default now()
);

-- Persistent chat history so the conversation survives a refresh.
create table if not exists coach_messages (
    id         uuid primary key default gen_random_uuid(),
    user_id    uuid not null references users(id) on delete cascade,
    role       text not null,            -- 'user' | 'assistant'
    content    text not null,
    created_at timestamptz not null default now()
);
create index if not exists idx_coach_messages_user
    on coach_messages (user_id, created_at);
