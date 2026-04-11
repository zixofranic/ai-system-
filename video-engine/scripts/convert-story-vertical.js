#!/usr/bin/env node
/**
 * Convert a condensed story into a 60-second vertical (9:16) Remotion timeline.
 *
 * Companion format to `convert-story.js` (horizontal 6-min version).
 * Uses the same sentence-build progressive captions as horizontal stories, but
 * the background is pan-and-scan across existing landscape art rather than a
 * separate image per scene.
 *
 * Usage:
 *   node convert-story-vertical.js \
 *     --script condensed_script.json \
 *     --timestamps timestamps.json \
 *     --art-paths art_paths.json \
 *     --voice voice.mp3 \
 *     --music C:/path/to/music.mp3 \
 *     --output 2026-04-09-story-vertical-seneca \
 *     --format story_vertical
 */

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

// --- Parse args ---
const args = {};
for (let i = 2; i < process.argv.length; i += 2) {
  const key = process.argv[i].replace(/^--/, "");
  args[key] = process.argv[i + 1];
}

const script = JSON.parse(fs.readFileSync(args.script, "utf-8"));
const timestamps = JSON.parse(fs.readFileSync(args.timestamps, "utf-8"));
const artPaths = JSON.parse(fs.readFileSync(args["art-paths"], "utf-8"));
const voicePath = args.voice;
const musicPath = args.music || null;
const outputName = args.output || "story-vertical";

const WIDTH = 1080;
const HEIGHT = 1920;
const FPS = 30;
const VOICE_START_MS = 500; // tight start for Shorts feed
const SENTENCE_ENDINGS = new Set([".", "!", "?", ";", ":"]);

// --- Voice timing ---
// Use the ACTUAL audio file duration (via ffprobe), NOT Whisper's last-word
// end time. Whisper often cuts the last 200-500ms of the final word's trailing
// phonemes, which made the Rumi vertical's "thirsty." get chopped.
function getAudioDurationMs(audioPath) {
  try {
    const out = execSync(
      `ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "${audioPath}"`,
      { encoding: "utf-8" },
    ).trim();
    return Math.round(parseFloat(out) * 1000);
  } catch (e) {
    console.warn(`ffprobe failed, falling back to Whisper timestamp: ${e.message}`);
    return Math.round(timestamps[timestamps.length - 1].end * 1000);
  }
}

const voiceFileEndMs = getAudioDurationMs(voicePath);
// +120ms safety pad so the very last phoneme doesn't clip on playback
const voiceEndMs = voiceFileEndMs + 120;
const totalDurationMs = voiceEndMs + VOICE_START_MS + 800; // 800ms tail

console.log(`Story vertical: ${WIDTH}x${HEIGHT}  duration ${(totalDurationMs / 1000).toFixed(1)}s`);

// --- Scene distribution ---
// We want 3-4 scenes for a ~60s video. Reuse landscape art paths in order.
const validArts = artPaths.filter((p) => p !== null);
const numScenes = Math.min(4, Math.max(2, validArts.length));
console.log(`Using ${numScenes} scene(s) from ${validArts.length} available art image(s)`);

// Even time split across scenes, snapped to sentence boundaries
function findSentenceBoundaries() {
  const boundaries = [{ wordIndex: 0, timeMs: 0 }];
  for (let i = 0; i < timestamps.length; i++) {
    const w = timestamps[i].word;
    const isEnd = [...SENTENCE_ENDINGS].some((p) => w.endsWith(p));
    if (isEnd) {
      boundaries.push({ wordIndex: i + 1, timeMs: timestamps[i].end * 1000 });
    }
  }
  return boundaries;
}

const sentenceBounds = findSentenceBoundaries();
const sceneBreaks = [];
const targetSceneDurationMs = voiceEndMs / numScenes;
for (let s = 1; s < numScenes; s++) {
  const idealMs = s * targetSceneDurationMs;
  let bestIdx = 0;
  let bestDist = Infinity;
  for (let b = 0; b < sentenceBounds.length; b++) {
    const d = Math.abs(sentenceBounds[b].timeMs - idealMs);
    if (d < bestDist) {
      bestDist = d;
      bestIdx = b;
    }
  }
  sceneBreaks.push(sentenceBounds[bestIdx].timeMs);
}

const sceneTimings = [];
let prevMs = 0;
for (let s = 0; s < numScenes; s++) {
  const startMs = prevMs;
  const endMs = s < numScenes - 1 ? sceneBreaks[s] : voiceEndMs;
  sceneTimings.push({ startMs, endMs });
  prevMs = endMs;
}

// --- Output directory ---
const destDir = path.join(__dirname, "..", "public", "content", outputName);
const imagesDir = path.join(destDir, "images");
const audioDir = path.join(destDir, "audio");
if (fs.existsSync(destDir)) fs.rmSync(destDir, { recursive: true });
fs.mkdirSync(imagesDir, { recursive: true });
fs.mkdirSync(audioDir, { recursive: true });

// --- Scene elements ---
const elements = [];
for (let i = 0; i < numScenes; i++) {
  const artPath = validArts[i % validArts.length];
  const imgName = `scene${i + 1}`;
  fs.copyFileSync(artPath, path.join(imagesDir, `${imgName}.png`));

  const { startMs, endMs } = sceneTimings[i];
  elements.push({
    startMs: Math.round(startMs + VOICE_START_MS),
    endMs: Math.round(endMs + VOICE_START_MS),
    imageUrl: imgName,
    enterTransition: "fade",
    exitTransition: "fade",
    animations: [],
  });
}

// First scene starts at 0 (covers any lead-in silence)
if (elements.length > 0) {
  elements[0].startMs = 0;
  elements[elements.length - 1].endMs = Math.round(totalDurationMs);
}

// --- Sentence-build progressive captions (reuses ProgressiveSubtitle) ---
// Vertical canvas is only 1080px wide vs 1920 for horizontal, so each
// caption chunk must be SHORTER — otherwise fitText shrinks the font
// dramatically to squeeze the full line into ~885px of usable width.
// 7 words max (vs 14 for horizontal) keeps the font large and readable.
const SENTENCE_HOLD_MS = 220;
const MAX_SENTENCE_WORDS = 7;
const BREAK_COMMA_AFTER = 4;
const textElements = [];

const flushSentence = (sentenceWords) => {
  if (sentenceWords.length === 0) return;
  const sentenceStartMs = Math.round(
    sentenceWords[0].start * 1000 + VOICE_START_MS,
  );
  const sentenceEndMs =
    Math.round(
      sentenceWords[sentenceWords.length - 1].end * 1000 + VOICE_START_MS,
    ) + SENTENCE_HOLD_MS;
  textElements.push({
    startMs: sentenceStartMs,
    endMs: sentenceEndMs,
    text: sentenceWords.map((w) => w.word).join(" ").toLowerCase(),
    position: "center",
    words: sentenceWords.map((w) => ({
      word: w.word.toLowerCase(),
      startMs: Math.round(w.start * 1000 + VOICE_START_MS),
      endMs: Math.round(w.end * 1000 + VOICE_START_MS),
    })),
  });
};

let sentence = [];
for (const w of timestamps) {
  sentence.push(w);
  const isEnd = [...SENTENCE_ENDINGS].some((p) => w.word.endsWith(p));
  const tooLong = sentence.length >= MAX_SENTENCE_WORDS;
  const breakAtComma = sentence.length >= BREAK_COMMA_AFTER && w.word.endsWith(",");
  if (isEnd || tooLong || breakAtComma) {
    flushSentence(sentence);
    sentence = [];
  }
}
flushSentence(sentence);

// Clip overlap between adjacent sentences
for (let i = 0; i < textElements.length - 1; i++) {
  if (textElements[i].endMs > textElements[i + 1].startMs) {
    textElements[i].endMs = textElements[i + 1].startMs;
  }
}

// --- Audio ---
const voiceDst = path.join(audioDir, "voice.mp3");
if (voicePath.toLowerCase().endsWith(".mp3")) {
  fs.copyFileSync(voicePath, voiceDst);
} else {
  // simple wav -> mp3 copy via ffmpeg would go here; the Python renderer
  // already converts, but keep a fallback for direct CLI usage
  fs.copyFileSync(voicePath, voiceDst);
}

const audioElements = [
  {
    startMs: VOICE_START_MS,
    endMs: Math.round(voiceEndMs + VOICE_START_MS),
    audioUrl: "voice",
  },
];

if (musicPath && fs.existsSync(musicPath)) {
  fs.copyFileSync(musicPath, path.join(audioDir, "music.mp3"));
  audioElements.push({
    startMs: 0,
    endMs: Math.round(totalDurationMs),
    audioUrl: "music",
  });
}

// --- Timeline + metadata ---
const timeline = {
  shortTitle: script.title,
  elements,
  text: textElements,
  audio: audioElements,
  metadata: {
    format: "story_vertical",
    width: WIDTH,
    height: HEIGHT,
    fps: FPS,
    philosopher: script.philosopher,
    channel:
      script.channel ||
      (script.philosopher === "Gibran" ? "gibran" : "wisdom"),
    watermark:
      script.philosopher === "Gibran"
        ? "Gibran Khalil Gibran"
        : "Deep Echoes of Wisdom",
    equalizerColor: script.equalizerColor || "#D4AF37",
  },
};

const metadata = {
  format: "story_vertical",
  width: WIDTH,
  height: HEIGHT,
  fps: FPS,
  philosopher: script.philosopher,
  channel:
    script.channel || (script.philosopher === "Gibran" ? "gibran" : "wisdom"),
  closingAttribution:
    script.closing_attribution || `Inspired by ${script.philosopher}`,
  watermark:
    script.philosopher === "Gibran"
      ? "Gibran Khalil Gibran"
      : "Deep Echoes of Wisdom",
};

fs.writeFileSync(
  path.join(destDir, "timeline.json"),
  JSON.stringify(timeline, null, 2),
);
fs.writeFileSync(
  path.join(destDir, "metadata.json"),
  JSON.stringify(metadata, null, 2),
);

console.log(`\nTimeline: ${path.join(destDir, "timeline.json")}`);
console.log(
  `Scenes: ${elements.length}  Captions: ${textElements.length}  Format: story_vertical (${WIDTH}x${HEIGHT})`,
);
