import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { z } from "zod";
import {
  DARK_GRADIENT_BOTTOM,
  DARK_GRADIENT_TOP,
  FPS,
  GOLD,
  MUSIC_FADE_FRAMES,
  MUSIC_VOLUME,
} from "../lib/constants";
import { TimelineSchema } from "../lib/types";
import { calculateFrameTiming, getAudioPath, getImagePath } from "../lib/utils";
import { Equalizer } from "./Equalizer";

export const shortVideoSchema = z.object({
  timeline: TimelineSchema.nullable(),
});

const EXTRA_SCALE = 0.15;

/**
 * ShortVideo — 9:16 portrait composition (1080x1920)
 *
 * Layout:
 *   - Full-screen background image with Ken Burns zoom
 *   - Top watermark
 *   - Dark gradient overlay at bottom half
 *   - Quote text, large, centered in lower half
 *   - Attribution below quote (gold, italic)
 *   - Voice narration + background music
 */
export const ShortVideo: React.FC<z.infer<typeof shortVideoSchema>> = ({
  timeline,
}) => {
  if (!timeline) {
    throw new Error("Expected timeline to be fetched");
  }

  const { id } = useVideoConfig();
  const watermark =
    timeline.metadata?.watermark || "Deep Echoes of Wisdom";
  const philosopher = timeline.metadata?.philosopher || "";

  // Two rendering modes — channel-gated, no length fallback:
  //   - "monologue"  — NA/AA only. Long character narrations (60-90s, 130+
  //                  words) scroll inside a fixed-height dark box with top
  //                  + bottom dark fade gradients.
  //   - "aphorism"   — Wisdom/Gibran ALWAYS. Short quotes get the signature
  //                  large centered text in a solid dark box. Long quotes
  //                  (the rare Dostoevsky / Gibran-Prophet exception) don't
  //                  fall back to MonologueOverlay anymore — that produced
  //                  unreadably small text. Instead, AphorismOverlay
  //                  auto-shrinks the font to fit (see component for the
  //                  exact taper).
  // History: 2026-04-26 added a length-based fallback that routed long
  // Wisdom/Gibran quotes through MonologueOverlay. Reverted 2026-04-27 —
  // user reported text became "barely legible". Replaced with auto-shrink
  // inside AphorismOverlay (see fitText logic in the component).
  const channel = (timeline.metadata?.channel || "").toLowerCase();

  // Separate text elements by role
  const quotes = timeline.text.filter(
    (t) => t.role === "quote" || !t.role,
  );

  const isMonologue = channel === "na" || channel === "aa";
  const attributions = timeline.text.filter(
    (t) => t.role === "attribution",
  );
  const captions = timeline.text.filter((t) => t.role === "caption");

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Background images with Ken Burns */}
      {timeline.elements.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
        );

        return (
          <Sequence
            key={`bg-${index}`}
            from={startFrame}
            durationInFrames={duration}
            premountFor={3 * FPS}
          >
            <ShortBackground project={id} item={element} />
          </Sequence>
        );
      })}

      {/* Dark gradients — ONLY for NA/AA monologue shorts where the
          scrolling text needs top + bottom fade bands. Aphorism shorts
          (Wisdom/Gibran) render over the clean art; their text sits in
          its own solid dark box and doesn't need the overlay. */}
      {isMonologue && (
        <>
          <AbsoluteFill style={{ background: DARK_GRADIENT_TOP, zIndex: 5 }} />
          <AbsoluteFill style={{ background: DARK_GRADIENT_BOTTOM, zIndex: 5 }} />
        </>
      )}

      {/* Watermark at top */}
      <AbsoluteFill
        style={{
          zIndex: 10,
          justifyContent: "flex-start",
          alignItems: "center",
          paddingTop: 60,
        }}
      >
        <div
          style={{
            fontSize: 28,
            color: "rgba(255,255,255,0.7)",
            fontFamily: "Georgia, serif",
            letterSpacing: 3,
            textTransform: "uppercase",
          }}
        >
          {watermark}
        </div>
      </AbsoluteFill>

      {/* Quote text — mode-specific overlay */}
      {quotes.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
        );

        return (
          <Sequence
            key={`quote-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            {isMonologue ? (
              <MonologueOverlay text={element.text} />
            ) : (
              <AphorismOverlay text={element.text} />
            )}
          </Sequence>
        );
      })}

      {/* Attribution */}
      {attributions.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
        );

        return (
          <Sequence
            key={`attr-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            <AttributionOverlay
              text={element.text}
              philosopher={philosopher}
            />
          </Sequence>
        );
      })}

      {/* Captions (subtitle style) */}
      {captions.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
        );

        return (
          <Sequence
            key={`cap-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            <ShortCaption text={element.text} />
          </Sequence>
        );
      })}

      {/* Audio */}
      {timeline.audio.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
        );

        const isMusic = element.audioUrl === "music";
        const fadeFrames = Math.min(MUSIC_FADE_FRAMES, Math.floor(duration / 4));

        return (
          <Sequence
            key={`audio-${index}`}
            from={startFrame}
            durationInFrames={duration}
            premountFor={3 * FPS}
          >
            <Audio
              src={staticFile(getAudioPath(id, element.audioUrl))}
              volume={
                isMusic
                  ? (f: number) => {
                      const fadeIn = Math.min(1, f / fadeFrames);
                      const fadeOut = Math.min(
                        1,
                        Math.max(0, (duration - f) / fadeFrames),
                      );
                      return Math.min(fadeIn, fadeOut) * MUSIC_VOLUME;
                    }
                  : 1
              }
            />
          </Sequence>
        );
      })}

      {/* Equalizer — pinned to bottom for shorts */}
      {timeline.audio
        .filter((el) => el.audioUrl !== "music")
        .slice(0, 1)
        .map((element, index) => {
          const { startFrame, duration } = calculateFrameTiming(
            element.startMs,
            element.endMs,
          );
          return (
            <Sequence
              key={`eq-${index}`}
              from={startFrame}
              durationInFrames={duration}
            >
              <AbsoluteFill
                style={{
                  zIndex: 15,
                  justifyContent: "flex-end",
                  alignItems: "center",
                  paddingBottom: 140,
                  pointerEvents: "none",
                }}
              >
                <Equalizer
                  audioSrc={staticFile(getAudioPath(id, element.audioUrl))}
                  color={timeline.metadata?.equalizerColor || GOLD}
                  numberOfBars={36}
                  maxBarHeight={140}
                  barWidth={8}
                  gap={6}
                />
              </AbsoluteFill>
            </Sequence>
          );
        })}
    </AbsoluteFill>
  );
};

// --- Sub-components ---

import type { BackgroundElement } from "../lib/types";

const ShortBackground: React.FC<{
  item: BackgroundElement;
  project: string;
}> = ({ item, project }) => {
  const frame = useCurrentFrame();
  const localMs = (frame / FPS) * 1000;

  // Source-aspect-agnostic: the Img fills the canvas with objectFit:cover,
  // and Ken Burns is applied via a CSS transform scale. This is correct for
  // any source aspect ratio (portrait 832x1216, landscape 1920x1080, etc.)
  // instead of the previous version which hardcoded landscape dimensions.
  let animScale = 1 + EXTRA_SCALE;

  const currentScaleAnim = item.animations?.find(
    (anim) =>
      anim.type === "scale" &&
      anim.startMs <= localMs &&
      anim.endMs >= localMs,
  );

  if (currentScaleAnim) {
    const progress =
      (localMs - currentScaleAnim.startMs) /
      (currentScaleAnim.endMs - currentScaleAnim.startMs);
    animScale =
      EXTRA_SCALE +
      progress * (currentScaleAnim.to - currentScaleAnim.from) +
      currentScaleAnim.from;
  }

  // Soft fade in/out
  const { durationInFrames } = useVideoConfig();
  const fadeFrames = Math.min(FPS, Math.floor(durationInFrames / 4));
  const opacity = interpolate(
    frame,
    [0, fadeFrames, durationInFrames - fadeFrames, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill style={{ opacity, overflow: "hidden" }}>
      <Img
        src={staticFile(getImagePath(project, item.imageUrl))}
        style={{
          width: "100%",
          height: "100%",
          objectFit: "cover",
          transform: `scale(${animScale})`,
          transformOrigin: "center center",
        }}
      />
    </AbsoluteFill>
  );
};

// AphorismOverlay — used for Wisdom/Gibran shorts.
//
// Short 1-3 sentence quotes (15-40 words, ~15-20s videos). Static text,
// centered in a solid dark rounded box over the art. No scroll, no alpha
// mask, no top/bottom gradients.
//
// Auto-shrink font for long quotes (added 2026-04-27). Wisdom/Gibran
// occasionally produce 50-80 word quotes (Dostoevsky paragraphs, full
// Gibran-Prophet passages). Previous fix routed those through
// MonologueOverlay's scrolling box, which used 50px text and felt
// "barely legible" per user. Now the Aphorism box stays the signature
// look but the font tapers down with length so the quote fits without
// scroll. Word-count-based taper is more predictable than fitText for
// our quote distribution.
function aphorismFontSize(words: number): number {
  if (words <= 25) return 64;   // signature look — most Wisdom/Gibran shorts
  if (words <= 40) return 56;
  if (words <= 60) return 48;
  if (words <= 80) return 42;   // Dostoevsky / long Gibran range
  return 38;                    // hard floor — anything longer should
                                // probably be a story, not a short
}

const AphorismOverlay: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 12,
  });

  const fadeOutFrames = Math.min(20, Math.floor(durationInFrames / 4));
  const fadeOut = interpolate(
    frame,
    [durationInFrames - fadeOutFrames, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const opacity = Math.min(interpolate(enter, [0, 1], [0, 1]), fadeOut);
  const translateY = interpolate(enter, [0, 1], [40, 0]);

  const wordCount = text.split(/\s+/).filter(Boolean).length;
  const fontSize = aphorismFontSize(wordCount);
  const lineHeight = Math.round(fontSize * 1.375);

  return (
    <AbsoluteFill
      style={{
        zIndex: 15,
        justifyContent: "center",
        alignItems: "center",
        padding: "0 50px",
      }}
    >
      <div
        style={{
          // Restored 2026-04-28 from 0.88 → 0.5. The Apr 17 split commit
          // (6ed43fa) bumped this to 0.88 by accident while splitting
          // Aphorism vs Monologue overlays — its message claimed it
          // restored the original look but the original was rgba(0,0,0,0.5)
          // (commit 0d7eaa4 from Mar 29). User noticed: "had a little
          // bit of nice transparency, not much but a hint" — that's the
          // 0.5 feel, where the art shows through subtly behind the box.
          backgroundColor: "rgba(0, 0, 0, 0.5)",
          borderRadius: 16,
          padding: "48px 52px",
          maxWidth: "95%",
          opacity,
          transform: `translateY(${translateY}px)`,
        }}
      >
        <div
          style={{
            fontSize,
            lineHeight: `${lineHeight}px`,
            color: "white",
            fontFamily: "Georgia, serif",
            fontWeight: "bold",
            fontStyle: "italic",
            textAlign: "center",
            textShadow: "0 2px 8px rgba(0,0,0,0.6)",
          }}
        >
          &ldquo;{text}&rdquo;
        </div>
      </div>
    </AbsoluteFill>
  );
};

// MonologueOverlay — used for NA/AA shorts.
//
// Fixed-height dark box with a top + bottom alpha fade. Long character
// narrations (60-90s, 130-175 words) scroll vertically over the sequence
// so lines gently fade in at the top edge and out at the bottom. Short
// quotes collapse the scroll (distance=0) but the mask still sits around
// invisible empty space.
//
// Line estimate is word-count-based because fitText-based estimation
// undercounts wrap and produced an overflow on the first AA render.
// Tuned 2026-04-18. Originally 54px / 1180px caused wall-of-text overflow.
// Then 42px / 1240px went too small AND the centered box bottom touched
// the attribution. Now: 50px font, 1340px box, top-anchored at y=160 so
// the bottom edge clears the attribution by ~120px.
const QUOTE_FONT = 50;
const QUOTE_LINE_RATIO = 1.4;
const QUOTE_BOX_HEIGHT = 1340;
// Side padding (left/right) for the inner text. Top/bottom padding is
// handled separately (see below) because the alpha-fade mask eats the
// first ~50px of the box on the top edge — without extra top inset the
// first line of text reads as half-transparent.
const QUOTE_H_PAD = 52;
// Top inset for the inner scrolling text. Sits BELOW the alpha-mask fade
// zone (4% of box height = ~54px) plus a 30px buffer so the first line of
// readable text lands at full opacity, not in the gradient.
const QUOTE_TOP_PAD = 90;
// Bottom inset is smaller — text scrolls THROUGH the bottom fade band on
// purpose (that's the whole point of the scroll), so we want the natural
// fade-out to work at the box bottom edge.
const QUOTE_BOTTOM_PAD = 60;
// Distance from canvas top to the box top. Picks up just below the
// watermark zone (y=60 paddingTop + ~50px text + breathing room).
const QUOTE_BOX_TOP_OFFSET = 160;
const QUOTE_SCROLL_START_PCT = 0.08;
const QUOTE_SCROLL_END_PCT = 0.92;
const WORDS_PER_LINE = 6;
const LINE_PAD_EXTRA = 1;

const MonologueOverlay: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 12,
  });

  const fadeOutFrames = Math.min(20, Math.floor(durationInFrames / 4));
  const fadeOut = interpolate(
    frame,
    [durationInFrames - fadeOutFrames, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const opacity = Math.min(interpolate(enter, [0, 1], [0, 1]), fadeOut);
  const translateY = interpolate(enter, [0, 1], [40, 0]);

  const quoted = `“${text}”`;

  // Word-count based line estimate. Robust against fitText oddities.
  const wordCount = quoted.split(/\s+/).filter(Boolean).length;
  const estimatedLines = Math.ceil(wordCount / WORDS_PER_LINE) + LINE_PAD_EXTRA;
  const textHeight = estimatedLines * QUOTE_FONT * QUOTE_LINE_RATIO;

  const visibleHeight = QUOTE_BOX_HEIGHT - QUOTE_TOP_PAD - QUOTE_BOTTOM_PAD;
  const scrollDistance = Math.max(0, textHeight - visibleHeight);

  // Scroll ramp — hold the first lines for ~8% of duration, scroll across
  // the middle, then hold the last lines at ~8% tail.
  const scrollStart = Math.floor(durationInFrames * QUOTE_SCROLL_START_PCT);
  const scrollEnd = Math.floor(durationInFrames * QUOTE_SCROLL_END_PCT);
  const scrollY = interpolate(
    frame,
    [scrollStart, scrollEnd],
    [0, -scrollDistance],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Mask removed 2026-04-18. The previous dual alpha-fade gradient meant
  // any line scrolling in/out of view was rendered at partial opacity, which
  // read as "broken text" on the dashboard preview rather than as a soft
  // edge. The dark backing box + overflow:hidden gives a clean hard clip:
  // text is either fully readable inside the box or clipped at the edge,
  // no in-between. The translateY scroll handles the motion; we don't need
  // the mask to "soften" the boundaries.
  const maskImage = "none";

  return (
    <AbsoluteFill
      style={{
        zIndex: 15,
        justifyContent: "flex-start",
        alignItems: "center",
        paddingTop: QUOTE_BOX_TOP_OFFSET,
        paddingLeft: 50,
        paddingRight: 50,
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.5)",
          borderRadius: 16,
          paddingTop: QUOTE_TOP_PAD,
          paddingBottom: QUOTE_BOTTOM_PAD,
          paddingLeft: QUOTE_H_PAD,
          paddingRight: QUOTE_H_PAD,
          maxWidth: "95%",
          height: QUOTE_BOX_HEIGHT,
          overflow: "hidden",
          opacity,
          transform: `translateY(${translateY}px)`,
          WebkitMaskImage: maskImage,
          maskImage,
        }}
      >
        <div
          style={{
            fontSize: QUOTE_FONT,
            lineHeight: `${Math.round(QUOTE_FONT * QUOTE_LINE_RATIO)}px`,
            color: "white",
            fontFamily: "Georgia, serif",
            fontWeight: "bold",
            fontStyle: "italic",
            textAlign: "center",
            textShadow: "0 2px 8px rgba(0,0,0,0.6)",
            transform: `translateY(${scrollY}px)`,
            willChange: "transform",
          }}
        >
          {quoted}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const AttributionOverlay: React.FC<{
  text: string;
  philosopher: string;
}> = ({ text, philosopher }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 12,
    delay: 5,
  });

  const fadeOutFrames = Math.min(20, Math.floor(durationInFrames / 4));
  const fadeOut = interpolate(
    frame,
    [durationInFrames - fadeOutFrames, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  const opacity = Math.min(interpolate(enter, [0, 1], [0, 1]), fadeOut);

  return (
    <AbsoluteFill
      style={{
        zIndex: 15,
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 300,
      }}
    >
      <div
        style={{
          fontSize: 40,
          color: GOLD,
          fontFamily: "Georgia, serif",
          fontStyle: "italic",
          textAlign: "center",
          opacity,
          textShadow: "0 2px 6px rgba(0,0,0,0.8)",
        }}
      >
        {text || `-- ${philosopher}`}
      </div>
    </AbsoluteFill>
  );
};

const ShortCaption: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 5,
  });

  const scaleVal = interpolate(enter, [0, 1], [0.8, 1]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 20,
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 100,
      }}
    >
      <div
        style={{
          fontSize: 40,
          color: "white",
          fontFamily: "Georgia, serif",
          fontWeight: "bold",
          textAlign: "center",
          textTransform: "uppercase",
          transform: `scale(${scaleVal})`,
          maxWidth: "85%",
          textShadow: "0 2px 8px rgba(0,0,0,0.9)",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};
