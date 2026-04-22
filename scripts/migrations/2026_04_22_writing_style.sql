-- Add writing_style column to content table.
-- Encodes the prose perspective (in_character vs narrator) — independent of TTS voice.
--
-- 'in_character' = AS the philosopher/persona/prophet (first-person). The voice IS them.
-- 'narrator'     = third-person narrator ABOUT them. Names them, paraphrases their position.
--
-- NULL = use channel default. Orchestrator falls back to gibran_essay_voice for legacy
-- Gibran rows, then to 'in_character' as final default. Custom Prompt rows ignore this
-- (pasted prose already encodes the style).
--
-- Step 1A of custom_prompts_v2_plan.md.
--
-- Run this once via the Supabase dashboard SQL editor:
-- https://supabase.com/dashboard/project/kwyqaewdvvdhodxieqrh/sql

ALTER TABLE content ADD COLUMN IF NOT EXISTS writing_style text;

ALTER TABLE content DROP CONSTRAINT IF EXISTS content_writing_style_check;
ALTER TABLE content ADD CONSTRAINT content_writing_style_check
  CHECK (writing_style IS NULL OR writing_style IN ('in_character', 'narrator'));

COMMENT ON COLUMN content.writing_style IS
  'Prose perspective for LLM-written content. in_character = AS the philosopher/persona/prophet (first-person). narrator = third-person narrator ABOUT them. NULL = channel default. Independent of TTS voice. Custom Prompt rows ignore this since pasted prose encodes the style.';
