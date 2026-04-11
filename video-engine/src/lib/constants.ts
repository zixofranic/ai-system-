export const FPS = 30;
export const INTRO_DURATION = 2 * FPS; // 2s intro
export const IMAGE_WIDTH = 1920;
export const IMAGE_HEIGHT = 1080;

// Background music volume (0-1). Lowered from 0.35 → 0.22 on 2026-04-08
// so the James Burton narration sits clearly on top.
export const MUSIC_VOLUME = 0.22;
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
