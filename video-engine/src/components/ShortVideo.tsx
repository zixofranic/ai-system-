import { Audio } from "@remotion/media";
import { fitText } from "@remotion/layout-utils";
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

  // Separate text elements by role
  const quotes = timeline.text.filter(
    (t) => t.role === "quote" || !t.role,
  );
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

      {/* Top gradient for watermark */}
      <AbsoluteFill
        style={{
          background: DARK_GRADIENT_TOP,
          zIndex: 5,
        }}
      />

      {/* Bottom gradient for text readability */}
      <AbsoluteFill
        style={{
          background: DARK_GRADIENT_BOTTOM,
          zIndex: 5,
        }}
      />

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

      {/* Quote text — centered in lower half */}
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
            <QuoteOverlay text={element.text} />
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

// Quote overlay auto-scrolls long text with a dual alpha mask (fade in at top,
// fade out at bottom). Keeps a comfortable reading font size (~56px) instead
// of shrinking the text when the quote is a monologue-length block — common
// for NA/AA recovery shorts where "the quote" is a full character passage.
//
// Short quotes (<= box height) don't scroll; the dual-mask is still applied
// but sits fully inside the black region so there's no visible fade.
const QUOTE_FONT = 56;
const QUOTE_LINE_RATIO = 1.42;
const QUOTE_BOX_WIDTH = 900; // inner text column width, matches padding math
const QUOTE_BOX_HEIGHT = 1200; // max vertical extent before scroll kicks in
const QUOTE_V_PAD = 56; // top/bottom internal padding of the black box
const QUOTE_SCROLL_START_PCT = 0.12;
const QUOTE_SCROLL_END_PCT = 0.92;

const QuoteOverlay: React.FC<{ text: string }> = ({ text }) => {
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

  // Estimate wrapped text height. fitText gives single-line width; divide by
  // our column width to estimate lines. Italic Georgia averages ~0.48em per
  // char, but fitText already accounts for that — we just need line count.
  const fitted = fitText({
    fontFamily: "Georgia, serif",
    fontStyle: "italic",
    fontWeight: "bold",
    text: quoted,
    withinWidth: QUOTE_BOX_WIDTH,
  });
  // fitted.fontSize is the size at which the text fits on ONE line within
  // QUOTE_BOX_WIDTH. Its actual width at that size is QUOTE_BOX_WIDTH.
  // Scale to our target font to find how wide it would be; divide by
  // column width to get an integer line count.
  const widthAtTarget = (QUOTE_BOX_WIDTH * QUOTE_FONT) / fitted.fontSize;
  const estimatedLines = Math.max(1, Math.ceil(widthAtTarget / QUOTE_BOX_WIDTH));
  const textHeight = estimatedLines * QUOTE_FONT * QUOTE_LINE_RATIO;

  const visibleHeight = QUOTE_BOX_HEIGHT - 2 * QUOTE_V_PAD;
  const scrollDistance = Math.max(0, textHeight - visibleHeight);
  const needsScroll = scrollDistance > 0;

  // When text fits, keep the box compact so it centers nicely over the art.
  const boxHeight = needsScroll
    ? QUOTE_BOX_HEIGHT
    : Math.min(QUOTE_BOX_HEIGHT, textHeight + 2 * QUOTE_V_PAD);

  // Scroll ramp — linger on the first lines, scroll through the middle, then
  // linger on the last lines so the viewer has time to read top and bottom.
  const scrollStart = Math.floor(durationInFrames * QUOTE_SCROLL_START_PCT);
  const scrollEnd = Math.floor(durationInFrames * QUOTE_SCROLL_END_PCT);
  const scrollY = needsScroll
    ? interpolate(frame, [scrollStart, scrollEnd], [0, -scrollDistance], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      })
    : 0;

  // Dual alpha mask — transparent at top 8%, transparent at bottom 8%, fully
  // visible in between. Creates the fade-in / fade-out band around the scroll.
  // Only applied when text actually scrolls so short quotes aren't faded.
  const maskImage = needsScroll
    ? "linear-gradient(180deg, transparent 0%, #000 10%, #000 90%, transparent 100%)"
    : undefined;

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
          backgroundColor: "rgba(0, 0, 0, 0.5)",
          borderRadius: 16,
          padding: `${QUOTE_V_PAD}px 52px`,
          maxWidth: "95%",
          height: boxHeight,
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
