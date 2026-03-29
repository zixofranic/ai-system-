#!/usr/bin/env node
/**
 * Convert midform video assets into a Remotion timeline.
 *
 * Input: multiple quotes (3-5), each with an image, voice narration, optional music.
 * Output: landscape 16:9 (1920x1080) timeline.json + metadata.json.
 *
 * Usage:
 *   node convert-midform.js \
 *     --script midform_script.json \
 *     --timestamps timestamps.json \
 *     --art-paths art_paths.json \
 *     --voice voice.mp3 \
 *     --music C:/AI/system/music/stoic_classical/stoic_01.mp3 \
 *     --output seneca-midform-001
 *
 * Script JSON format:
 * {
 *   "title": "Seneca on the Shortness of Life",
 *   "philosopher": "Seneca",
 *   "channel": "wisdom",
 *   "quotes": [
 *     {
 *       "text": "It is not that we have a short time to live...",
 *       "attribution": "Seneca, On the Shortness of Life",
 *       "narration": "In a letter to Paulinus, Seneca reminds us..."
 *     },
 *     ...
 *   ]
 * }
 *
 * Timestamps JSON (word-level from TTS):
 * [{ "word": "In", "start": 0.1, "end": 0.2 }, ...]
 *
 * Art paths JSON: array of image file paths (one per quote).
 */

const fs = require("fs");
const path = require("path");

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
const outputName = args.output || "midform";

const WIDTH = 1920;
const HEIGHT = 1080;
const FPS = 30;
const VOICE_START_MS = 2000;
const SENTENCE_ENDINGS = new Set([".", "!", "?", ";", ":"]);

// --- Calculate total voice duration ---
const voiceEndMs = timestamps[timestamps.length - 1].end * 1000;
const totalDurationMs = voiceEndMs + VOICE_START_MS + 3000;
const numQuotes = script.quotes.length;
const numScenes = artPaths.filter((p) => p !== null).length;
const targetSceneDurationMs = voiceEndMs / numScenes;

console.log(`Midform format: ${WIDTH}x${HEIGHT}`);
console.log(`Total duration: ${(totalDurationMs / 1000).toFixed(1)}s`);
console.log(`Quotes: ${numQuotes}, Scenes: ${numScenes}`);
console.log(`Target per scene: ${(targetSceneDurationMs / 1000).toFixed(1)}s`);

// --- Find sentence boundaries in word timestamps ---
function findSentenceBoundaries() {
  const boundaries = [{ wordIndex: 0, timeMs: 0 }];
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

const sceneTimings = distributeScenes();

for (let i = 0; i < sceneTimings.length; i++) {
  const dur = (sceneTimings[i].endMs - sceneTimings[i].startMs) / 1000;
  console.log(
    `  Scene ${i + 1}: ${(sceneTimings[i].startMs / 1000).toFixed(1)}s - ${(sceneTimings[i].endMs / 1000).toFixed(1)}s (${dur.toFixed(1)}s)`,
  );
}

// --- Output directory ---
const destDir = path.join(__dirname, "..", "public", "content", outputName);
const imagesDir = path.join(destDir, "images");
const audioDir = path.join(destDir, "audio");
fs.mkdirSync(imagesDir, { recursive: true });
fs.mkdirSync(audioDir, { recursive: true });

// --- Scene image elements ---
const elements = [];
let validArtIdx = 0;
for (let i = 0; i < numScenes; i++) {
  while (validArtIdx < artPaths.length && !artPaths[validArtIdx]) validArtIdx++;
  if (validArtIdx >= artPaths.length) break;

  const artPath = artPaths[validArtIdx];
  validArtIdx++;

  const { startMs, endMs } = sceneTimings[i];
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
  elements[elements.length - 1].animations[0].endMs = Math.round(totalDurationMs);
}

// --- Text elements ---
const textElements = [];

// Quote overlays — distribute across the scenes
for (let q = 0; q < numQuotes && q < numScenes; q++) {
  const quote = script.quotes[q];
  const scene = sceneTimings[q];
  const sceneDur = scene.endMs - scene.startMs;

  // Quote appears in the middle third of the scene
  const quoteStartMs = scene.startMs + sceneDur * 0.1 + VOICE_START_MS;
  const quoteEndMs = scene.startMs + sceneDur * 0.7 + VOICE_START_MS;

  textElements.push({
    startMs: Math.round(quoteStartMs),
    endMs: Math.round(quoteEndMs),
    text: quote.text,
    position: "center",
    role: "quote",
    attribution: quote.attribution || script.philosopher,
  });

  // Narration text (smaller) — appears after quote fades
  if (quote.narration) {
    const narrStartMs = scene.startMs + sceneDur * 0.72 + VOICE_START_MS;
    const narrEndMs = scene.endMs + VOICE_START_MS;

    textElements.push({
      startMs: Math.round(narrStartMs),
      endMs: Math.round(narrEndMs),
      text: quote.narration,
      position: "center",
      role: "narration",
    });
  }
}

// Captions from timestamps
const MAX_CAPTION_WORDS = 8;
let chunk = [];

for (const w of timestamps) {
  chunk.push(w);
  const atPunct = [...SENTENCE_ENDINGS].some((p) => w.word.endsWith(p));
  if ((atPunct && chunk.length >= 2) || chunk.length >= MAX_CAPTION_WORDS) {
    const text = chunk.map((c) => c.word).join(" ").toLowerCase();
    const startMs = chunk[0].start * 1000 + VOICE_START_MS;
    let endMs = chunk[chunk.length - 1].end * 1000 + VOICE_START_MS;
    if (endMs - startMs < 800) endMs = startMs + 800;

    textElements.push({
      startMs: Math.round(startMs),
      endMs: Math.round(endMs),
      text,
      position: "bottom",
      role: "caption",
    });
    chunk = [];
  }
}
if (chunk.length > 0) {
  const text = chunk.map((c) => c.word).join(" ").toLowerCase();
  textElements.push({
    startMs: Math.round(chunk[0].start * 1000 + VOICE_START_MS),
    endMs: Math.round(chunk[chunk.length - 1].end * 1000 + VOICE_START_MS),
    text,
    position: "bottom",
    role: "caption",
  });
}

// --- Audio ---
fs.copyFileSync(voicePath, path.join(audioDir, "voice.mp3"));
if (musicPath && fs.existsSync(musicPath)) {
  fs.copyFileSync(musicPath, path.join(audioDir, "music.mp3"));
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
  metadata: {
    format: "midform",
    width: WIDTH,
    height: HEIGHT,
    fps: FPS,
    philosopher: script.philosopher,
    channel: script.channel || (script.philosopher === "Gibran" ? "gibran" : "wisdom"),
    watermark:
      script.philosopher === "Gibran"
        ? "Gibran Khalil Gibran"
        : "Deep Echoes of Wisdom",
  },
};

const metadata = {
  format: "midform",
  width: WIDTH,
  height: HEIGHT,
  fps: FPS,
  philosopher: script.philosopher,
  channel: script.channel || (script.philosopher === "Gibran" ? "gibran" : "wisdom"),
  closingAttribution: script.closing_attribution || `Inspired by ${script.philosopher}`,
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
console.log(`Scenes: ${elements.length}, Quotes: ${numQuotes}`);
console.log(`Captions: ${textElements.filter((t) => t.role === "caption").length}`);
console.log(`Format: midform (${WIDTH}x${HEIGHT})`);
console.log(`\nPreview: http://localhost:3001/${outputName}`);
console.log(
  `Render:  npx remotion render ${outputName} --output="${outputName}.mp4"`,
);
