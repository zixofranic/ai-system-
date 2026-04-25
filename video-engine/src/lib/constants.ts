export const FPS = 30;
export const INTRO_DURATION = 2 * FPS; // 2s intro
export const IMAGE_WIDTH = 1920;
export const IMAGE_HEIGHT = 1080;

// Background music volume (0-1). Lowered from 0.35 → 0.22 (2026-04-08),
// 0.22 → 0.16 (2026-04-17), and 0.16 → 0.10 (2026-04-25) — user
// reported music was too loud on the Wisdom Sun Tzu midform after
// extending cinematic to all channels. Voice sits at 1.0 (Remotion
// max), so dropping the music floor is the only lever for "louder
// voice" perception.
export const MUSIC_VOLUME = 0.10;
// How many frames to fade music in at start and out at end
export const MUSIC_FADE_FRAMES = 2 * FPS; // 2 seconds

// Portrait (shorts)
export const SHORT_WIDTH = 1080;
export const SHORT_HEIGHT = 1920;

// Colors
export const GOLD = "#D4AF37";
export const DARK_OVERLAY = "rgba(0, 0, 0, 0.55)";
export const DARK_GRADIENT_BOTTOM =
  "linear-gradient(to top, rgba(0,0,0,0.85) 0%, rgba(0,0,0,0.4) 50%, transparent 100%)";
export const DARK_GRADIENT_TOP =
  "linear-gradient(to bottom, rgba(0,0,0,0.6) 0%, transparent 40%)";
