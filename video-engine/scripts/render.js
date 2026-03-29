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
  console.log(`\n=== Converting story data ===`);
  const convertArgs = [
    `--script "${args.script}"`,
    `--timestamps "${args.timestamps}"`,
    `--art-paths "${args["art-paths"]}"`,
    `--voice "${args.voice}"`,
    args.music ? `--music "${args.music}"` : "",
    `--output "${compositionId}"`,
    `--format "${args.format || "story"}"`,
  ]
    .filter(Boolean)
    .join(" ");

  execSync(`node "${path.join(__dirname, "convert-story.js")}" ${convertArgs}`, {
    stdio: "inherit",
    cwd: projectRoot,
  });
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
