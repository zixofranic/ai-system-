#!/usr/bin/env node
/**
 * Convert story assets into a Remotion timeline with evenly distributed scenes.
 *
 * Scene timing: total duration / num_scenes, snapped to sentence boundaries.
 * Each scene image reflects what's being narrated during that time window.
 *
 * Usage:
 *   node convert-story.js \
 *     --script story_script.json \
 *     --timestamps timestamps.json \
 *     --art-paths art_paths.json \
 *     --voice voice.mp3 \
 *     --music C:/AI/system/music/stoic_classical/stoic_01.mp3 \
 *     --output epictetus-detroit \
 *     --format story
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
const outputName = args.output || "story";
const format = args.format || "story"; // story | short | midform | longform

// Scene-first path: when the Python pipeline emitted scene timings derived
// from the storyboarded scenes, use those exact boundaries instead of
// even-splitting the audio. Each image then aligns with the narration
// words for its own scene rather than landing on a mechanical chunk.
// Falls back to even-split when the file is absent (legacy scripts).
const sceneTimingsRaw = args["scene-timings"]
  ? JSON.parse(fs.readFileSync(args["scene-timings"], "utf-8"))
  : null;

const VOICE_START_MS = 2000;
const SENTENCE_ENDINGS = new Set([".", "!", "?", ";", ":"]);

// --- Format configs ---
const FORMAT_CONFIG = {
  short: { width: 1080, height: 1920, fps: 30 },
  story: { width: 1920, height: 1080, fps: 30 },
  midform: { width: 1920, height: 1080, fps: 30 },
  longform: { width: 1920, height: 1080, fps: 30 },
};

const config = FORMAT_CONFIG[format] || FORMAT_CONFIG.story;

// --- Calculate total voice duration ---
// Use actual audio file duration via ffprobe (Whisper often misses the
// final 200-500ms of the last word's trailing phonemes).
function getAudioDurationMs(audioPath) {
  try {
    const out = execSync(
      `ffprobe -v error -show_entries format=duration -of default=nk=1:nw=1 "${audioPath}"`,
      { encoding: "utf-8" },
    ).trim();
    return Math.round(parseFloat(out) * 1000);
  } catch (e) {
    console.warn(`ffprobe failed, using Whisper timestamp: ${e.message}`);
    return Math.round(timestamps[timestamps.length - 1].end * 1000);
  }
}

const voiceFileEndMs = getAudioDurationMs(voicePath);
// +120ms safety pad so the very last phoneme doesn't clip on playback
const voiceEndMs = voiceFileEndMs + 120;
const totalDurationMs = voiceEndMs + VOICE_START_MS + 3000;
const numScenes = artPaths.filter((p) => p !== null).length;
const targetSceneDurationMs = voiceEndMs / numScenes;

console.log(`Total duration: ${(totalDurationMs / 1000).toFixed(1)}s`);
console.log(`Scenes: ${numScenes}`);
if (sceneTimingsRaw) {
  console.log(`Mode: scene-first (using ${sceneTimingsRaw.length} explicit timings)`);
} else {
  console.log(`Mode: even-split. Target per scene: ${(targetSceneDurationMs / 1000).toFixed(1)}s`);
}

// --- Find sentence boundaries in word timestamps ---
// Returns array of { wordIndex, timeMs } for each sentence end
function findSentenceBoundaries() {
  const boundaries = [{ wordIndex: 0, timeMs: 0 }]; // start
  for (let i = 0; i < timestamps.length; i++) {
    const word = timestamps[i].word;
    const isSentenceEnd = [...SENTENCE_ENDINGS].some((p) => word.endsWith(p));
    if (isSentenceEnd) {
      boundaries.push({
        wordIndex: i + 1,
        timeMs: timestamps[i].end * 1000,
      });
    }
  }
  return boundaries;
}

// --- Distribute scenes evenly, snapping to sentence boundaries ---
function distributeScenes() {
  const sentenceBounds = findSentenceBoundaries();
  const sceneBreaks = [];

  for (let s = 1; s < numScenes; s++) {
    const idealTimeMs = s * targetSceneDurationMs;

    // Find closest sentence boundary to this ideal time
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let b = 0; b < sentenceBounds.length; b++) {
      const dist = Math.abs(sentenceBounds[b].timeMs - idealTimeMs);
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = b;
      }
    }
    sceneBreaks.push(sentenceBounds[bestIdx].timeMs);
  }

  // Build scene time ranges
  const scenes = [];
  let prevMs = 0;
  for (let s = 0; s < numScenes; s++) {
    const startMs = prevMs;
    const endMs = s < numScenes - 1 ? sceneBreaks[s] : voiceEndMs;
    scenes.push({ startMs, endMs });
    prevMs = endMs;
  }

  return scenes;
}

// --- Use explicit scene timings from the Python pipeline (scene-first mode) ---
// `sceneTimingsRaw` holds one entry per storyboarded scene; the number of
// rendered scenes is bounded by the number of valid art paths. If the
// counts mismatch (e.g., one image failed to generate), we trim or pad to
// match `numScenes` so every art path still gets a window.
function useExplicitTimings() {
  const trimmed = sceneTimingsRaw.slice(0, numScenes);
  // Pad: if we have fewer timings than images (image-count > scene-count
  // shouldn't happen with scene-first, but guard anyway), share the last
  // window.
  while (trimmed.length < numScenes) {
    const last = trimmed[trimmed.length - 1] || { startMs: 0, endMs: voiceEndMs };
    trimmed.push({ startMs: last.endMs, endMs: voiceEndMs });
  }
  // Force the last window to extend to voiceEndMs so we never leave a gap
  // of silence between final image and audio end.
  trimmed[trimmed.length - 1].endMs = voiceEndMs;
  // Force the first window to start at 0 so we never leave a gap at the head.
  trimmed[0].startMs = 0;
  return trimmed;
}

const sceneTimings = sceneTimingsRaw ? useExplicitTimings() : distributeScenes();

// Log distribution
for (let i = 0; i < sceneTimings.length; i++) {
  const dur = (sceneTimings[i].endMs - sceneTimings[i].startMs) / 1000;
  console.log(
    `  Scene ${i + 1}: ${(sceneTimings[i].startMs / 1000).toFixed(1)}s - ${(sceneTimings[i].endMs / 1000).toFixed(1)}s (${dur.toFixed(1)}s)`,
  );
}

// --- Output directory ---
const destDir = path.join(
  __dirname,
  "..",
  "public",
  "content",
  outputName,
);
const imagesDir = path.join(destDir, "images");
const audioDir = path.join(destDir, "audio");
fs.mkdirSync(imagesDir, { recursive: true });
fs.mkdirSync(audioDir, { recursive: true });

// --- Scene image elements ---
const elements = [];
let validArtIdx = 0;
for (let i = 0; i < numScenes; i++) {
  // Find next valid art path
  while (validArtIdx < artPaths.length && !artPaths[validArtIdx]) validArtIdx++;
  if (validArtIdx >= artPaths.length) break;

  const artPath = artPaths[validArtIdx];
  validArtIdx++;

  const { startMs, endMs } = sceneTimings[i];

  // Copy image
  const imgName = `scene${i + 1}`;
  fs.copyFileSync(artPath, path.join(imagesDir, `${imgName}.png`));

  elements.push({
    startMs: Math.round(startMs + VOICE_START_MS),
    endMs: Math.round(endMs + VOICE_START_MS),
    imageUrl: imgName,
    enterTransition: "fade",
    exitTransition: "fade",
    animations: [
      {
        type: "scale",
        from: i % 2 === 0 ? 1.0 : 1.06,
        to: i % 2 === 0 ? 1.06 : 1.0,
        startMs: Math.round(startMs + VOICE_START_MS),
        endMs: Math.round(endMs + VOICE_START_MS),
      },
    ],
  });
}

// First scene starts at 0 (covers intro)
if (elements.length > 0) {
  elements[0].startMs = 0;
  elements[0].animations[0].startMs = 0;
}
// Last scene extends to total duration
if (elements.length > 0) {
  elements[elements.length - 1].endMs = Math.round(totalDurationMs);
  elements[elements.length - 1].animations[0].endMs =
    Math.round(totalDurationMs);
}

// --- Caption text elements ---
// Sentence-build captions: each sentence is ONE textElement that holds for
// the whole sentence duration. Within it, individual word timings let
// ProgressiveSubtitle fade words in one by one (eyes have a stable anchor
// instead of a single flickering word).
//
// Sentences are split at punctuation (.!?;:). Long sentences (>14 words) are
// hard-broken at the next comma or word boundary so font-fitting stays sane.
const SENTENCE_HOLD_MS = 350; // hold the full sentence on-screen after the last word
const MAX_SENTENCE_WORDS = 14;
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
    text: sentenceWords
      .map((w) => w.word)
      .join(" ")
      .toLowerCase(),
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
  const isSentenceEnd = [...SENTENCE_ENDINGS].some((p) => w.word.endsWith(p));
  const tooLong = sentence.length >= MAX_SENTENCE_WORDS;
  const breakAtComma = sentence.length >= 10 && w.word.endsWith(",");

  if (isSentenceEnd || tooLong || breakAtComma) {
    flushSentence(sentence);
    sentence = [];
  }
}
flushSentence(sentence);

// Prevent overlap: clip each sentence's endMs so it doesn't bleed into the next
for (let i = 0; i < textElements.length - 1; i++) {
  if (textElements[i].endMs > textElements[i + 1].startMs) {
    textElements[i].endMs = textElements[i + 1].startMs;
  }
}

// --- Audio ---
fs.copyFileSync(voicePath, path.join(audioDir, "voice.mp3"));
if (musicPath && fs.existsSync(musicPath)) {
  // Duck the music across the final seconds of the narration so the closing
  // line rings out clear instead of being buried. 100% until voiceEnd-4s,
  // linear ramp to 30% over 1.5s, hold at 30% through the tail fade.
  const duckStartSec = Math.max(0.5, (voiceEndMs - 4000) / 1000);
  const rampSec = 1.5;
  const target = 0.3;
  const drop = 1 - target; // 0.7
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
}

const audioElements = [
  {
    startMs: VOICE_START_MS,
    endMs: Math.round(voiceEndMs + VOICE_START_MS),
    audioUrl: "voice",
  },
];

if (musicPath && fs.existsSync(musicPath)) {
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
};

// Gibran detection — philosopher name is sometimes "Gibran", sometimes
// "Gibran Khalil Gibran". Case-insensitive substring match catches both.
// Previously `=== "Gibran"` failed on the full name and Gibran videos
// incorrectly got the "Deep Echoes of Wisdom" watermark.
const isGibran = (script.philosopher || "").toLowerCase().includes("gibran")
  || (script.channel || "").toLowerCase() === "gibran";

const metadata = {
  format,
  width: config.width,
  height: config.height,
  fps: config.fps,
  philosopher: script.philosopher,
  channel: isGibran ? "gibran" : "wisdom",
  closingAttribution: script.closing_attribution,
  watermark: isGibran ? "Gibran Khalil Gibran" : "Deep Echoes of Wisdom",
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
console.log(`Scenes: ${elements.length}, Captions: ${textElements.length}`);
console.log(`Format: ${format} (${config.width}x${config.height})`);
console.log(`\nPreview: http://localhost:3001/${outputName}`);
console.log(
  `Render:  npx remotion render ${outputName} --output="${outputName}.mp4"`,
);
