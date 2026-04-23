-- Web Push subscription store for PWA notifications.
-- One user can have multiple subscriptions (phone + desktop + tablet).
-- CTO Phase 3 of pwa_responsive_plan — push fires from content_poller.py
-- directly via pywebpush when a row transitions to status='ready'.
--
-- Run via the Supabase dashboard SQL editor:
-- https://supabase.com/dashboard/project/kwyqaewdvvdhodxieqrh/sql

CREATE TABLE IF NOT EXISTS push_subscriptions (
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  -- Fields from the Web Push API's PushSubscription.toJSON():
  endpoint   text NOT NULL,
  p256dh     text NOT NULL,  -- client public key
  auth       text NOT NULL,  -- shared secret for payload encryption
  user_agent text,           -- optional — helps debug which device subscribed
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  -- One subscription per (user, endpoint). If the same device
  -- re-subscribes (token refresh), upsert replaces the old row.
  UNIQUE (user_id, endpoint)
);

CREATE INDEX IF NOT EXISTS idx_push_subscriptions_user ON push_subscriptions(user_id);

-- RLS — a user can only read/insert/delete their own subscriptions.
-- The poller uses the service role (bypasses RLS) to send to all subs.
ALTER TABLE push_subscriptions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "push_sub_self_read" ON push_subscriptions;
CREATE POLICY "push_sub_self_read" ON push_subscriptions
  FOR SELECT USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "push_sub_self_write" ON push_subscriptions;
CREATE POLICY "push_sub_self_write" ON push_subscriptions
  FOR INSERT WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "push_sub_self_delete" ON push_subscriptions;
CREATE POLICY "push_sub_self_delete" ON push_subscriptions
  FOR DELETE USING (auth.uid() = user_id);

-- No UPDATE policy by design — endpoint changes = upsert via INSERT.

COMMENT ON TABLE push_subscriptions IS
  'Web Push subscriptions. Users consent via PushOptIn component after first Queue Week. Poller (content_poller.py) reads via service role to broadcast status=ready notifications.';
