import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
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
  FPS,
  GOLD,
  INTRO_DURATION,
} from "../lib/constants";
import { TimelineSchema } from "../lib/types";
import {
  calculateFrameTiming,
  getAudioPath,
} from "../lib/utils";
import { Background } from "./Background";
import { Equalizer } from "./Equalizer";

export const midformVideoSchema = z.object({
  timeline: TimelineSchema.nullable(),
});

/**
 * MidformVideo — 16:9 landscape (1920x1080)
 *
 * Layout:
 *   - Multiple quotes (3-5), each with its own background image
 *   - Quote text large, centered on screen
 *   - Transition narration text (smaller) between quotes
 *   - Ken Burns on backgrounds (reuses Background component)
 *   - Captions for voice narration
 *   - Intro title card
 *   - Voice + background music audio
 */
export const MidformVideo: React.FC<z.infer<typeof midformVideoSchema>> = ({
  timeline,
}) => {
  if (!timeline) {
    throw new Error("Expected timeline to be fetched");
  }

  const { id } = useVideoConfig();
  const watermark =
    timeline.metadata?.watermark || "Deep Echoes of Wisdom";

  const quotes = timeline.text.filter((t) => t.role === "quote");
  const narrations = timeline.text.filter((t) => t.role === "narration");
  const captions = timeline.text.filter(
    (t) => t.role === "caption" || !t.role,
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Intro title card */}
      <Sequence durationInFrames={INTRO_DURATION}>
        <MidformIntro title={timeline.shortTitle} watermark={watermark} />
      </Sequence>

      {/* Background images with Ken Burns */}
      {timeline.elements.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { includeIntro: index === 0 },
        );

        return (
          <Sequence
            key={`bg-${index}`}
            from={startFrame}
            durationInFrames={duration}
            premountFor={3 * FPS}
          >
            <Background project={id} item={element} />
          </Sequence>
        );
      })}

      {/* Dark overlay for text readability */}
      <AbsoluteFill
        style={{
          background: DARK_GRADIENT_BOTTOM,
          zIndex: 5,
        }}
      />

      {/* Quote overlays — large, centered */}
      {quotes.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { addIntroOffset: true },
        );

        return (
          <Sequence
            key={`quote-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            <MidformQuote
              text={element.text}
              attribution={element.attribution}
            />
          </Sequence>
        );
      })}

      {/* Narration text — smaller, between quotes */}
      {narrations.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { addIntroOffset: true },
        );

        return (
          <Sequence
            key={`narr-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            <MidformNarration text={element.text} />
          </Sequence>
        );
      })}

      {/* Captions (subtitle-style at bottom) */}
      {captions.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { addIntroOffset: true },
        );

        return (
          <Sequence
            key={`cap-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            <MidformCaption text={element.text} />
          </Sequence>
        );
      })}

      {/* Audio */}
      {timeline.audio.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { addIntroOffset: true },
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
              volume={isMusic ? 0.35 : 1}
            />
          </Sequence>
        );
      })}

      {/* Equalizer — vertically centered for midform */}
      {timeline.audio
        .filter((el) => el.audioUrl !== "music")
        .map((element, index) => {
          const { startFrame, duration } = calculateFrameTiming(
            element.startMs,
            element.endMs,
            { addIntroOffset: true },
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
                  justifyContent: "center",
                  alignItems: "center",
                  pointerEvents: "none",
                }}
              >
                <Equalizer
                  audioSrc={staticFile(getAudioPath(id, element.audioUrl))}
                  color={timeline.metadata?.equalizerColor || GOLD}
                  numberOfBars={48}
                  maxBarHeight={160}
                  barWidth={10}
                  gap={8}
                />
              </AbsoluteFill>
            </Sequence>
          );
        })}
    </AbsoluteFill>
  );
};

// --- Sub-components ---

const MidformIntro: React.FC<{
  title: string;
  watermark: string;
}> = ({ title, watermark }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 10,
  });

  const opacity = interpolate(enter, [0, 1], [0, 1]);
  const scaleVal = interpolate(enter, [0, 1], [0.9, 1]);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "black",
        justifyContent: "center",
        alignItems: "center",
        zIndex: 20,
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 30,
          opacity,
          transform: `scale(${scaleVal})`,
        }}
      >
        <div
          style={{
            fontSize: 24,
            color: GOLD,
            fontFamily: "Georgia, serif",
            letterSpacing: 4,
            textTransform: "uppercase",
          }}
        >
          {watermark}
        </div>
        <div
          style={{
            fontSize: 72,
            lineHeight: "84px",
            color: "white",
            fontFamily: "Georgia, serif",
            textAlign: "center",
            maxWidth: "80%",
            textShadow: "0 2px 12px rgba(0,0,0,0.8)",
          }}
        >
          {title}
        </div>
        <div
          style={{
            width: 120,
            height: 2,
            backgroundColor: GOLD,
            marginTop: 10,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

const MidformQuote: React.FC<{
  text: string;
  attribution?: string;
}> = ({ text, attribution }) => {
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
  const translateY = interpolate(enter, [0, 1], [30, 0]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 15,
        justifyContent: "center",
        alignItems: "center",
        padding: "0 120px",
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.55)",
          borderRadius: 16,
          padding: "50px 60px",
          maxWidth: "85%",
          opacity,
          transform: `translateY(${translateY}px)`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 24,
        }}
      >
        <div
          style={{
            fontSize: 56,
            lineHeight: "76px",
            color: "white",
            fontFamily: "Georgia, serif",
            fontWeight: "bold",
            fontStyle: "italic",
            textAlign: "center",
            textShadow: "0 2px 8px rgba(0,0,0,0.5)",
          }}
        >
          &ldquo;{text}&rdquo;
        </div>
        {attribution && (
          <div
            style={{
              fontSize: 32,
              color: GOLD,
              fontFamily: "Georgia, serif",
              fontStyle: "italic",
              textShadow: "0 1px 4px rgba(0,0,0,0.6)",
            }}
          >
            -- {attribution}
          </div>
        )}
      </div>
    </AbsoluteFill>
  );
};

const MidformNarration: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 10,
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
        zIndex: 12,
        justifyContent: "center",
        alignItems: "center",
        padding: "0 200px",
      }}
    >
      <div
        style={{
          fontSize: 38,
          lineHeight: "54px",
          color: "rgba(255,255,255,0.9)",
          fontFamily: "Georgia, serif",
          textAlign: "center",
          opacity,
          textShadow: "0 2px 10px rgba(0,0,0,0.8)",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};

const MidformCaption: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 5,
  });

  const scaleVal = interpolate(enter, [0, 1], [0.8, 1]);
  const translateY = interpolate(enter, [0, 1], [30, 0]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 20,
        justifyContent: "flex-end",
        alignItems: "center",
        paddingBottom: 60,
      }}
    >
      <div
        style={{
          fontSize: 44,
          color: "white",
          fontFamily: "Georgia, serif",
          fontWeight: "bold",
          textAlign: "center",
          textTransform: "uppercase",
          transform: `scale(${scaleVal}) translateY(${translateY}px)`,
          maxWidth: "85%",
          textShadow: "0 2px 8px rgba(0,0,0,0.9)",
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};
