#!/usr/bin/env node
/**
 * Convert short-form video assets into a Remotion timeline.
 *
 * Input: a single quote, single background image, voice narration, optional music.
 * Output: portrait 9:16 (1080x1920) timeline.json + metadata.json.
 *
 * Usage:
 *   node convert-short.js \
 *     --script short_script.json \
 *     --image C:/path/to/background.png \
 *     --voice voice.mp3 \
 *     --music C:/AI/system/music/ambient/track_01.mp3 \
 *     --output gibran-short-001 \
 *     --timestamps timestamps.json
 *
 * Script JSON format:
 * {
 *   "title": "On Love",
 *   "quote": "Love gives naught but itself...",
 *   "philosopher": "Gibran Khalil Gibran",
 *   "attribution": "-- Gibran Khalil Gibran, The Prophet",
 *   "channel": "gibran"
 * }
 *
 * Timestamps JSON (word-level from TTS):
 * [{ "word": "Love", "start": 0.1, "end": 0.4 }, ...]
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
const imagePath = args.image;
const voicePath = args.voice;
const musicPath = args.music || null;
const outputName = args.output || "short";
const timestampsPath = args.timestamps || null;

const timestamps = timestampsPath
  ? JSON.parse(fs.readFileSync(timestampsPath, "utf-8"))
  : null;

const WIDTH = 1080;
const HEIGHT = 1920;
const FPS = 30;
const VOICE_START_MS = 500; // Shorts start faster
const QUOTE_APPEAR_MS = 300;
const SENTENCE_ENDINGS = new Set([".", "!", "?", ";", ":"]);

// --- Calculate durations ---
let voiceEndMs;
if (timestamps && timestamps.length > 0) {
  voiceEndMs = timestamps[timestamps.length - 1].end * 1000;
} else {
  // Estimate from file size (~16kbps for compressed speech)
  const stat = fs.statSync(voicePath);
  voiceEndMs = (stat.size / 16000) * 1000; // rough estimate
  console.warn("No timestamps provided; estimating duration from file size.");
}

const totalDurationMs = voiceEndMs + VOICE_START_MS + 2000; // 2s tail

console.log(`Short format: ${WIDTH}x${HEIGHT}`);
console.log(`Total duration: ${(totalDurationMs / 1000).toFixed(1)}s`);

// --- Output directory ---
const destDir = path.join(__dirname, "..", "public", "content", outputName);
const imagesDir = path.join(destDir, "images");
const audioDir = path.join(destDir, "audio");
fs.mkdirSync(imagesDir, { recursive: true });
fs.mkdirSync(audioDir, { recursive: true });

// --- Single background element (full duration, Ken Burns) ---
const imgName = "scene1";
fs.copyFileSync(imagePath, path.join(imagesDir, `${imgName}.png`));

const elements = [
  {
    startMs: 0,
    endMs: Math.round(totalDurationMs),
    imageUrl: imgName,
    enterTransition: "fade",
    exitTransition: "fade",
    animations: [
      {
        type: "scale",
        from: 1.0,
        to: 1.12,
        startMs: 0,
        endMs: Math.round(totalDurationMs),
      },
    ],
  },
];

// --- Text elements ---
const textElements = [];

// Quote — displayed in lower half for most of the video
const quoteStartMs = VOICE_START_MS + QUOTE_APPEAR_MS;
const quoteEndMs = voiceEndMs + VOICE_START_MS;

textElements.push({
  startMs: Math.round(quoteStartMs),
  endMs: Math.round(quoteEndMs),
  text: script.quote,
  position: "center",
  role: "quote",
});

// Attribution — appears slightly after quote, stays until end
const attrStartMs = quoteStartMs + 1500;
textElements.push({
  startMs: Math.round(attrStartMs),
  endMs: Math.round(quoteEndMs),
  text: script.attribution || `-- ${script.philosopher}`,
  position: "bottom",
  role: "attribution",
});

// Captions from timestamps (word-level subtitles)
if (timestamps && timestamps.length > 0) {
  const MAX_CAPTION_WORDS = 6;
  let chunk = [];

  for (const w of timestamps) {
    chunk.push(w);
    const atPunct = [...SENTENCE_ENDINGS].some((p) => w.word.endsWith(p));
    if ((atPunct && chunk.length >= 2) || chunk.length >= MAX_CAPTION_WORDS) {
      const text = chunk.map((c) => c.word).join(" ").toLowerCase();
      const startMs = chunk[0].start * 1000 + VOICE_START_MS;
      let endMs = chunk[chunk.length - 1].end * 1000 + VOICE_START_MS;
      if (endMs - startMs < 600) endMs = startMs + 600;

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
    format: "short",
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
  format: "short",
  width: WIDTH,
  height: HEIGHT,
  fps: FPS,
  philosopher: script.philosopher,
  channel: script.channel || (script.philosopher === "Gibran" ? "gibran" : "wisdom"),
  closingAttribution: script.attribution || `-- ${script.philosopher}`,
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
console.log(`Format: short (${WIDTH}x${HEIGHT})`);
console.log(`Quote: "${script.quote.substring(0, 60)}..."`);
console.log(`Captions: ${textElements.filter((t) => t.role === "caption").length}`);
console.log(`\nPreview: http://localhost:3001/${outputName}`);
console.log(
  `Render:  npx remotion render ${outputName} --output="${outputName}.mp4"`,
);
