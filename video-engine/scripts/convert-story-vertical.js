#!/usr/bin/env node
/**
 * Convert a standalone 60-second vertical story into a 9:16 Remotion timeline.
 *
 * Each art path in art-paths.json is ONE full portrait image for ONE scene
 * (matches the scene-first Python pipeline, which generates fresh 832x1216
 * SDXL art per scene — no more pan-and-scan of landscape art).
 *
 * When --scene-timings is provided, uses those exact boundaries (derived
 * from each scene's narration via Whisper alignment). Otherwise falls back
 * to even-split snapped to sentence boundaries.
 *
 * Usage:
 *   node convert-story-vertical.js \
 *     --script script.json \
 *     --timestamps timestamps.json \
 *     --art-paths art_paths.json \
 *     --scene-timings scene_timings.json \
 *     --voice voice.mp3 \
 *     --music C:/path/to/music.mp3 \
 *     --output 2026-04-14-story-vertical-seneca \
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
const sceneTimingsRaw = args["scene-timings"]
  ? JSON.parse(fs.readFileSync(args["scene-timings"], "utf-8"))
  : null;

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
// 2000ms tail — ~1.5s of final image holding after the voice stops, then the
// last element's exitTransition fade plays out. 800ms was too abrupt; user
// reported it felt like the video cut mid-breath.
const TAIL_MS = 2000;
const totalDurationMs = voiceEndMs + VOICE_START_MS + TAIL_MS;

console.log(`Story vertical: ${WIDTH}x${HEIGHT}  duration ${(totalDurationMs / 1000).toFixed(1)}s`);

// --- Scene distribution ---
// One art path = one scene. Scene-first pipeline generates the exact number
// of portrait images needed.
const validArts = artPaths.filter((p) => p !== null);
const numScenes = validArts.length;
if (numScenes === 0) {
  console.error("No valid art paths — cannot build timeline");
  process.exit(1);
}
console.log(
  `Using ${numScenes} scene(s) — ${sceneTimingsRaw ? "explicit timings (scene-first)" : "even-split fallback"}`,
);

// Even-split fallback (only used when --scene-timings not provided)
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

function distributeEven() {
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
  const out = [];
  let prev = 0;
  for (let s = 0; s < numScenes; s++) {
    const startMs = prev;
    const endMs = s < numScenes - 1 ? sceneBreaks[s] : voiceEndMs;
    out.push({ startMs, endMs });
    prev = endMs;
  }
  return out;
}

// Use explicit scene timings when Python provided them; trim/pad to match
// the actual image count and snap the first/last to 0/voiceEndMs so there
// is never a gap at the head or tail of the video.
function useExplicitTimings() {
  const trimmed = sceneTimingsRaw.slice(0, numScenes);
  while (trimmed.length < numScenes) {
    const last = trimmed[trimmed.length - 1] || { startMs: 0, endMs: voiceEndMs };
    trimmed.push({ startMs: last.endMs, endMs: voiceEndMs });
  }
  trimmed[0].startMs = 0;
  trimmed[trimmed.length - 1].endMs = voiceEndMs;
  return trimmed;
}

const sceneTimings = sceneTimingsRaw ? useExplicitTimings() : distributeEven();

// --- Output directory ---
const destDir = path.join(__dirname, "..", "public", "content", outputName);
const imagesDir = path.join(destDir, "images");
const audioDir = path.join(destDir, "audio");
if (fs.existsSync(destDir)) fs.rmSync(destDir, { recursive: true });
fs.mkdirSync(imagesDir, { recursive: true });
fs.mkdirSync(audioDir, { recursive: true });

// --- Scene elements ---
// One portrait image per scene — no reuse, no pan-and-scan. Each image is
// already 832x1216 (scales to 1080x1920). Add a gentle Ken-Burns zoom so
// static shorts don't feel frozen.
const elements = [];
for (let i = 0; i < numScenes; i++) {
  const artPath = validArts[i];
  const imgName = `scene${i + 1}`;
  fs.copyFileSync(artPath, path.join(imagesDir, `${imgName}.png`));

  const { startMs, endMs } = sceneTimings[i];
  const startAbs = Math.round(startMs + VOICE_START_MS);
  const endAbs = Math.round(endMs + VOICE_START_MS);
  elements.push({
    startMs: startAbs,
    endMs: endAbs,
    imageUrl: imgName,
    enterTransition: "fade",
    exitTransition: "fade",
    animations: [
      {
        type: "scale",
        from: i % 2 === 0 ? 1.0 : 1.05,
        to: i % 2 === 0 ? 1.05 : 1.0,
        startMs: startAbs,
        endMs: endAbs,
      },
    ],
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
  // Duck the music so the closing line is heard clearly, not buried under
  // strings. 100% until voiceEnd-3s, linear ramp to 30% over 1s, hold 30%
  // through tail. Shorter ramp than horizontal since 60s videos have less
  // room to breathe.
  const duckStartSec = Math.max(0.5, (voiceEndMs - 3000) / 1000);
  const rampSec = 1.0;
  const target = 0.3;
  const drop = 1 - target;
  const volExpr =
    `if(lt(t,${duckStartSec}),1,` +
    `if(lt(t,${duckStartSec + rampSec}),1-${drop}*(t-${duckStartSec})/${rampSec},` +
    `${target}))`;
  const musicDst = path.join(audioDir, "music.mp3");
  try {
    execSync(
      `ffmpeg -y -i "${musicPath}" -af "volume='${volExpr}':eval=frame" ` +
        `-codec:a libmp3lame -b:a 192k "${musicDst}"`,
      { stdio: "pipe" },
    );
    console.log(`Music ducked (100% -> ${target * 100}% at ${duckStartSec.toFixed(1)}s over ${rampSec}s)`);
  } catch (e) {
    console.warn(`Music ducking failed (${e.message}); falling back to plain copy`);
    fs.copyFileSync(musicPath, musicDst);
  }
  audioElements.push({
    startMs: 0,
    endMs: Math.round(totalDurationMs),
    audioUrl: "music",
  });
}

const { resolveChannelMeta } = require("./lib/channel-meta");
const { channel, watermark } = resolveChannelMeta(script);

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
    channel,
    watermark,
    equalizerColor: script.equalizerColor || "#D4AF37",
  },
};

const metadata = {
  format: "story_vertical",
  width: WIDTH,
  height: HEIGHT,
  fps: FPS,
  philosopher: script.philosopher,
  channel,
  closingAttribution:
    script.closing_attribution || `Inspired by ${script.philosopher}`,
  watermark,
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
