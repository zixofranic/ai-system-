#!/usr/bin/env node
/**
 * Convert longform video assets into a Remotion timeline.
 *
 * Input: chapter-based script with multiple images per chapter.
 * Output: landscape 16:9 (1920x1080) timeline.json + metadata.json.
 *
 * Usage:
 *   node convert-longform.js \
 *     --script longform_script.json \
 *     --timestamps timestamps.json \
 *     --art-paths art_paths.json \
 *     --voice voice.mp3 \
 *     --music C:/AI/system/music/stoic_classical/stoic_01.mp3 \
 *     --output marcus-longform-001
 *
 * Script JSON format:
 * {
 *   "title": "Marcus Aurelius: Meditations on Death",
 *   "philosopher": "Marcus Aurelius",
 *   "channel": "wisdom",
 *   "chapters": [
 *     {
 *       "title": "The Impermanence of All Things",
 *       "narration": "Marcus Aurelius, writing from the battlefield...",
 *       "quotes": [
 *         {
 *           "text": "Think of yourself as dead...",
 *           "attribution": "Marcus Aurelius, Meditations VII.56"
 *         }
 *       ],
 *       "imageCount": 3
 *     },
 *     ...
 *   ]
 * }
 *
 * Timestamps JSON: [{ "word": "Marcus", "start": 0.1, "end": 0.4 }, ...]
 *
 * Art paths JSON: flat array of image file paths, distributed across chapters.
 * The script's chapters[].imageCount tells how many images belong to each chapter.
 * If imageCount is omitted, images are distributed evenly.
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
const outputName = args.output || "longform";

const WIDTH = 1920;
const HEIGHT = 1080;
const FPS = 30;
const VOICE_START_MS = 2000;
const CHAPTER_TITLE_MS = 3000; // 3 seconds for chapter title card
const SENTENCE_ENDINGS = new Set([".", "!", "?", ";", ":"]);

// --- Calculate total voice duration ---
const voiceEndMs = timestamps[timestamps.length - 1].end * 1000;
const numChapters = script.chapters.length;
const chapterTitleTotalMs = numChapters * CHAPTER_TITLE_MS;
const totalDurationMs = voiceEndMs + VOICE_START_MS + chapterTitleTotalMs + 3000;
const validArtPaths = artPaths.filter((p) => p !== null);
const numScenes = validArtPaths.length;

console.log(`Longform format: ${WIDTH}x${HEIGHT}`);
console.log(`Total duration: ${(totalDurationMs / 1000).toFixed(1)}s`);
console.log(`Chapters: ${numChapters}, Total scenes: ${numScenes}`);

// --- Distribute images per chapter ---
function getImagesPerChapter() {
  const counts = [];
  let allocated = 0;

  for (let c = 0; c < numChapters; c++) {
    const ch = script.chapters[c];
    if (ch.imageCount && ch.imageCount > 0) {
      counts.push(ch.imageCount);
      allocated += ch.imageCount;
    } else {
      counts.push(0); // mark for auto-distribution
    }
  }

  // Auto-distribute remaining images
  const autoChapters = counts.filter((c) => c === 0).length;
  const remaining = numScenes - allocated;
  if (autoChapters > 0 && remaining > 0) {
    const perChapter = Math.floor(remaining / autoChapters);
    let leftover = remaining - perChapter * autoChapters;
    for (let c = 0; c < counts.length; c++) {
      if (counts[c] === 0) {
        counts[c] = perChapter + (leftover > 0 ? 1 : 0);
        if (leftover > 0) leftover--;
      }
    }
  }

  return counts;
}

const imagesPerChapter = getImagesPerChapter();
console.log(`Images per chapter: ${imagesPerChapter.join(", ")}`);

// --- Find sentence boundaries ---
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

// --- Distribute chapter timing ---
// Each chapter gets a proportional share of voice time based on image count
function distributeChapters() {
  const sentenceBounds = findSentenceBoundaries();
  const totalImages = imagesPerChapter.reduce((a, b) => a + b, 0);
  const chapters = [];
  let prevMs = 0;

  for (let c = 0; c < numChapters; c++) {
    const proportion = imagesPerChapter[c] / totalImages;
    const idealEndMs = prevMs + voiceEndMs * proportion;

    // Snap to sentence boundary
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let b = 0; b < sentenceBounds.length; b++) {
      const dist = Math.abs(sentenceBounds[b].timeMs - idealEndMs);
      if (dist < bestDist) {
        bestDist = dist;
        bestIdx = b;
      }
    }

    const endMs =
      c < numChapters - 1 ? sentenceBounds[bestIdx].timeMs : voiceEndMs;

    chapters.push({ startMs: prevMs, endMs });
    prevMs = endMs;
  }

  return chapters;
}

const chapterTimings = distributeChapters();

for (let c = 0; c < chapterTimings.length; c++) {
  const dur = (chapterTimings[c].endMs - chapterTimings[c].startMs) / 1000;
  console.log(
    `  Chapter ${c + 1} "${script.chapters[c].title}": ${(chapterTimings[c].startMs / 1000).toFixed(1)}s - ${(chapterTimings[c].endMs / 1000).toFixed(1)}s (${dur.toFixed(1)}s, ${imagesPerChapter[c]} images)`,
  );
}

// --- Output directory ---
const destDir = path.join(__dirname, "..", "public", "content", outputName);
const imagesDir = path.join(destDir, "images");
const audioDir = path.join(destDir, "audio");
fs.mkdirSync(imagesDir, { recursive: true });
fs.mkdirSync(audioDir, { recursive: true });

// --- Build timeline ---
const elements = [];
const textElements = [];
let sceneIdx = 0;
let artIdx = 0;

// Cumulative chapter title offset — each chapter title card pushes timing forward
let chapterTitleOffset = 0;

for (let c = 0; c < numChapters; c++) {
  const chapter = script.chapters[c];
  const chTiming = chapterTimings[c];
  const numImages = imagesPerChapter[c];
  const chDuration = chTiming.endMs - chTiming.startMs;
  const sceneLength = numImages > 0 ? chDuration / numImages : chDuration;

  // Chapter title card
  const titleStartMs = chTiming.startMs + VOICE_START_MS + chapterTitleOffset;
  const titleEndMs = titleStartMs + CHAPTER_TITLE_MS;

  textElements.push({
    startMs: Math.round(titleStartMs),
    endMs: Math.round(titleEndMs),
    text: chapter.title,
    position: "center",
    role: "chapter-title",
  });

  chapterTitleOffset += CHAPTER_TITLE_MS;

  // Scene images for this chapter
  for (let s = 0; s < numImages; s++) {
    // Skip null art paths
    while (artIdx < artPaths.length && !artPaths[artIdx]) artIdx++;
    if (artIdx >= artPaths.length) break;

    const artPath = artPaths[artIdx];
    artIdx++;

    const sceneStart = chTiming.startMs + s * sceneLength;
    const sceneEnd =
      s < numImages - 1
        ? chTiming.startMs + (s + 1) * sceneLength
        : chTiming.endMs;

    const imgName = `scene${sceneIdx + 1}`;
    fs.copyFileSync(artPath, path.join(imagesDir, `${imgName}.png`));

    const adjustedStart = sceneStart + VOICE_START_MS + chapterTitleOffset;
    const adjustedEnd = sceneEnd + VOICE_START_MS + chapterTitleOffset;

    elements.push({
      startMs: Math.round(adjustedStart),
      endMs: Math.round(adjustedEnd),
      imageUrl: imgName,
      enterTransition: "fade",
      exitTransition: "fade",
      animations: [
        {
          type: "scale",
          from: sceneIdx % 2 === 0 ? 1.0 : 1.06,
          to: sceneIdx % 2 === 0 ? 1.06 : 1.0,
          startMs: Math.round(adjustedStart),
          endMs: Math.round(adjustedEnd),
        },
      ],
    });

    sceneIdx++;
  }

  // Quote overlays for this chapter
  if (chapter.quotes) {
    const quoteDuration =
      chDuration / Math.max(chapter.quotes.length, 1) * 0.5;

    for (let q = 0; q < chapter.quotes.length; q++) {
      const quote = chapter.quotes[q];
      const quoteOffset =
        (q / chapter.quotes.length) * chDuration + chDuration * 0.1;
      const quoteStartMs =
        chTiming.startMs + quoteOffset + VOICE_START_MS + chapterTitleOffset;
      const quoteEndMs = quoteStartMs + quoteDuration;

      textElements.push({
        startMs: Math.round(quoteStartMs),
        endMs: Math.round(quoteEndMs),
        text: quote.text,
        position: "center",
        role: "quote",
        attribution: quote.attribution || script.philosopher,
      });
    }
  }

  // Narration text for this chapter
  if (chapter.narration) {
    const narrStartMs =
      chTiming.startMs + VOICE_START_MS + chapterTitleOffset + CHAPTER_TITLE_MS;
    const narrEndMs = narrStartMs + Math.min(chDuration * 0.25, 5000);

    textElements.push({
      startMs: Math.round(narrStartMs),
      endMs: Math.round(narrEndMs),
      text: chapter.narration,
      position: "center",
      role: "narration",
    });
  }
}

// First element starts at 0 (covers intro)
if (elements.length > 0) {
  elements[0].startMs = 0;
  elements[0].animations[0].startMs = 0;
}
// Last element extends to total duration
if (elements.length > 0) {
  elements[elements.length - 1].endMs = Math.round(totalDurationMs);
  elements[elements.length - 1].animations[0].endMs = Math.round(totalDurationMs);
}

// --- Captions from timestamps ---
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
    format: "longform",
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
  format: "longform",
  width: WIDTH,
  height: HEIGHT,
  fps: FPS,
  philosopher: script.philosopher,
  channel: script.channel || (script.philosopher === "Gibran" ? "gibran" : "wisdom"),
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
console.log(`Scenes: ${elements.length}, Chapters: ${numChapters}`);
console.log(`Quotes: ${textElements.filter((t) => t.role === "quote").length}`);
console.log(`Captions: ${textElements.filter((t) => t.role === "caption").length}`);
console.log(`Format: longform (${WIDTH}x${HEIGHT})`);
console.log(`\nPreview: http://localhost:3001/${outputName}`);
console.log(
  `Render:  npx remotion render ${outputName} --output="${outputName}.mp4"`,
);
