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
  SHORT_HEIGHT,
  SHORT_WIDTH,
} from "../lib/constants";
import { TimelineSchema } from "../lib/types";
import { calculateFrameTiming, getAudioPath, getImagePath } from "../lib/utils";

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

        return (
          <Sequence
            key={`audio-${index}`}
            from={startFrame}
            durationInFrames={duration}
            premountFor={3 * FPS}
          >
            <Audio
              src={staticFile(getAudioPath(id, element.audioUrl))}
              volume={isMusic ? 0.15 : 1}
            />
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
  const { width, height } = useVideoConfig();

  // For portrait: cover the 1080x1920 frame
  const coverScale = Math.max(
    width / SHORT_WIDTH,
    height / SHORT_HEIGHT,
  );

  // Use image natural ratio — we assume source images may be landscape
  // so we scale to cover the portrait frame
  const imgWidth = 1920; // assume source image dimensions
  const imgHeight = 1080;
  const fitScale = Math.max(width / imgWidth, height / imgHeight);
  const scaledW = imgWidth * fitScale;
  const scaledH = imgHeight * fitScale;

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

  const finalW = scaledW * animScale;
  const finalH = scaledH * animScale;
  const top = -(finalH - height) / 2;
  const left = -(finalW - width) / 2;

  return (
    <AbsoluteFill>
      <Img
        src={staticFile(getImagePath(project, item.imageUrl))}
        style={{
          width: finalW,
          height: finalH,
          position: "absolute",
          top,
          left,
          objectFit: "cover",
        }}
      />
    </AbsoluteFill>
  );
};

const QuoteOverlay: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 8,
  });

  const opacity = interpolate(enter, [0, 1], [0, 1]);
  const translateY = interpolate(enter, [0, 1], [40, 0]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 15,
        justifyContent: "center",
        alignItems: "center",
        top: "40%",
        height: "50%",
        padding: "0 60px",
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.5)",
          borderRadius: 16,
          padding: "40px 48px",
          maxWidth: "90%",
          opacity,
          transform: `translateY(${translateY}px)`,
        }}
      >
        <div
          style={{
            fontSize: 52,
            lineHeight: "72px",
            color: "white",
            fontFamily: "Georgia, serif",
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

const AttributionOverlay: React.FC<{
  text: string;
  philosopher: string;
}> = ({ text, philosopher }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 10,
    delay: 5,
  });

  const opacity = interpolate(enter, [0, 1], [0, 1]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 15,
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 200,
      }}
    >
      <div
        style={{
          fontSize: 36,
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
          textAlign: "center",
          textTransform: "uppercase",
          WebkitTextStroke: "1.5px rgba(0,0,0,0.8)",
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
