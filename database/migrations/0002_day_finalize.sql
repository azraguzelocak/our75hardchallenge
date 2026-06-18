-- ===========================================================================
-- 75 Hard tracker — migration 0002
-- Adds a "finalized" flag to daily_logs.
--
-- Why: day_passed defaults to false, which is also the state of a day that is
-- still in progress. We need to tell "the day is over and was failed" apart
-- from "the day is not finished yet". `finalized` makes that explicit and lets
-- the pass/fail engine apply the day counter exactly once per day.
--
-- Run this in the Supabase SQL editor after 0001_init.sql.
-- ===========================================================================

alter table daily_logs
    add column if not exists finalized boolean not null default false;

-- Helps the lazy roll-over query that finds unfinalized past days.
create index if not exists idx_daily_logs_user_finalized
    on daily_logs (user_id, date) where finalized = false;
