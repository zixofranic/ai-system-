-- content_preferences — promote / avoid signals that bias what Claude
-- (and Ollama) writes for next week's plan.
--
-- Why: the analytics tab on the dashboard lets Ziad mark a philosopher,
-- topic_category, or specific topic as "promote" (do more) or "avoid"
-- (do less / stop) on a given channel. ai_writer.py reads these rows
-- when seeding next week's plan and when generating quotes/scripts,
-- biasing the prompt soft-style — not as a hard quota. Aligns with the
-- "don't auto-queue" rule: signals shape suggestions, the human still
-- approves.
--
-- One row per (channel, axis, value) tuple. Upsert pattern: the
-- dashboard server action does ON CONFLICT DO UPDATE so the toggle
-- behavior in the UI is idempotent.
--
-- "neutral" is represented by row deletion, not a third signal value,
-- so a filtered query for biased dimensions stays cheap (rows = signal
-- count, not philosopher count).

create table if not exists content_preferences (
  id           uuid primary key default gen_random_uuid(),
  channel_id   uuid not null references channels(id) on delete cascade,
  -- Exactly one of these three is non-null per row (the axis). Enforced
  -- by check constraint below — keeps queries simple (filter by axis,
  -- get back values).
  philosopher      text,
  topic_category   text,
  topic            text,
  signal       text not null check (signal in ('promote', 'avoid')),
  reason       text,
  created_by   uuid references auth.users(id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),

  constraint content_preferences_one_axis
    check (
      (case when philosopher    is not null then 1 else 0 end) +
      (case when topic_category is not null then 1 else 0 end) +
      (case when topic          is not null then 1 else 0 end)
      = 1
    )
);

-- Uniqueness: one signal per (channel, axis, value). The COALESCE trick
-- lets us put all three axis columns into one composite unique index
-- without three partial indexes.
create unique index if not exists content_preferences_unique_axis
  on content_preferences (
    channel_id,
    coalesce(philosopher, ''),
    coalesce(topic_category, ''),
    coalesce(topic, '')
  );

create index if not exists content_preferences_channel_signal
  on content_preferences (channel_id, signal);

-- RLS — same pattern as content: admin or has channel access reads/writes
-- their own channel's prefs. Public auth isn't an issue, the dashboard
-- middleware already redirects unauthenticated users.
alter table content_preferences enable row level security;

create policy content_preferences_select on content_preferences
  for select using (
    is_admin() or can_access_channel(channel_id)
  );

create policy content_preferences_insert on content_preferences
  for insert with check (
    is_admin() or can_access_channel(channel_id)
  );

create policy content_preferences_update on content_preferences
  for update using (
    is_admin() or can_access_channel(channel_id)
  ) with check (
    is_admin() or can_access_channel(channel_id)
  );

create policy content_preferences_delete on content_preferences
  for delete using (
    is_admin() or can_access_channel(channel_id)
  );

-- Touch updated_at on update so the writer can tell when a signal
-- changed (useful for cache invalidation when the writer keeps a local
-- copy in-process for a 5-min poller tick).
create or replace function content_preferences_touch_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists content_preferences_touch on content_preferences;
create trigger content_preferences_touch
  before update on content_preferences
  for each row execute function content_preferences_touch_updated_at();
