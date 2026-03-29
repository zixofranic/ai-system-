#!/usr/bin/env node
/**
 * Automated render — converts story data and renders via Remotion.
 *
 * Usage:
 *   node render.js \
 *     --script story_script.json \
 *     --timestamps timestamps.json \
 *     --art-paths art_paths.json \
 *     --voice voice.mp3 \
 *     --output epictetus-detroit \
 *     --format story \
 *     --out-dir C:/AI/wisdom/output/stories
 *
 * Or with --composition to render an existing composition:
 *   node render.js --composition epictetus-detroit --out-dir C:/AI/wisdom/output/stories
 */

const { execSync } = require("child_process");
const path = require("path");
const fs = require("fs");

const args = {};
for (let i = 2; i < process.argv.length; i += 2) {
  args[process.argv[i].replace(/^--/, "")] = process.argv[i + 1];
}

const projectRoot = path.join(__dirname, "..");
const outDir = args["out-dir"] || path.join(projectRoot, "output");
fs.mkdirSync(outDir, { recursive: true });

let compositionId = args.composition;

// Step 1: Convert if we have source data
if (!compositionId && args.script) {
  compositionId = args.output || "story";
  const format = args.format || "story";

  // Route to format-specific converter
  const converterMap = {
    story: "convert-story.js",
    short: "convert-short.js",
    midform: "convert-midform.js",
    longform: "convert-longform.js",
  };
  const converterFile = converterMap[format] || converterMap.story;

  console.log(`\n=== Converting ${format} data ===`);

  // Build args based on format
  const convertArgs = [];
  convertArgs.push(`--script "${args.script}"`);
  if (args.timestamps) convertArgs.push(`--timestamps "${args.timestamps}"`);
  if (args["art-paths"]) convertArgs.push(`--art-paths "${args["art-paths"]}"`);
  if (args.image) convertArgs.push(`--image "${args.image}"`);
  if (args.voice) convertArgs.push(`--voice "${args.voice}"`);
  if (args.music) convertArgs.push(`--music "${args.music}"`);
  convertArgs.push(`--output "${compositionId}"`);
  if (format !== "short") convertArgs.push(`--format "${format}"`);

  execSync(
    `node "${path.join(__dirname, converterFile)}" ${convertArgs.join(" ")}`,
    {
      stdio: "inherit",
      cwd: projectRoot,
    },
  );
}

if (!compositionId) {
  console.error("Provide --composition or --script to render");
  process.exit(1);
}

// Step 2: Read metadata for format
const metaPath = path.join(
  projectRoot,
  "public",
  "content",
  compositionId,
  "metadata.json",
);
let width = 1920,
  height = 1080;
if (fs.existsSync(metaPath)) {
  const meta = JSON.parse(fs.readFileSync(metaPath, "utf-8"));
  width = meta.width;
  height = meta.height;
}

// Step 3: Render
const outputFile = path.join(outDir, `${compositionId}.mp4`);
console.log(`\n=== Rendering ${compositionId} (${width}x${height}) ===`);
console.log(`Output: ${outputFile}\n`);

try {
  execSync(
    `npx remotion render ${compositionId} "${outputFile}" --width=${width} --height=${height} --codec=h264 --crf=18`,
    {
      stdio: "inherit",
      cwd: projectRoot,
      timeout: 600000, // 10 min
    },
  );
  console.log(`\n=== DONE: ${outputFile} ===`);
} catch (e) {
  console.error(`Render failed: ${e.message}`);
  process.exit(1);
}
