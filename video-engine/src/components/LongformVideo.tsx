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

export const longformVideoSchema = z.object({
  timeline: TimelineSchema.nullable(),
});

/**
 * LongformVideo — 16:9 landscape (1920x1080)
 *
 * Chapter-based structure:
 *   - Intro title card
 *   - Chapter title cards between sections
 *   - Multiple images per chapter
 *   - Quote overlays, narration text, captions
 *   - Same Ken Burns / audio as other formats
 */
export const LongformVideo: React.FC<
  z.infer<typeof longformVideoSchema>
> = ({ timeline }) => {
  if (!timeline) {
    throw new Error("Expected timeline to be fetched");
  }

  const { id } = useVideoConfig();
  const watermark =
    timeline.metadata?.watermark || "Deep Echoes of Wisdom";

  const chapterTitles = timeline.text.filter(
    (t) => t.role === "chapter-title",
  );
  const quotes = timeline.text.filter((t) => t.role === "quote");
  const narrations = timeline.text.filter((t) => t.role === "narration");
  const captions = timeline.text.filter(
    (t) => t.role === "caption" || !t.role,
  );

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Intro title card */}
      <Sequence durationInFrames={INTRO_DURATION}>
        <LongformIntro title={timeline.shortTitle} watermark={watermark} />
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

      {/* Dark overlay */}
      <AbsoluteFill
        style={{
          background: DARK_GRADIENT_BOTTOM,
          zIndex: 5,
        }}
      />

      {/* Chapter title cards */}
      {chapterTitles.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { addIntroOffset: true },
        );

        return (
          <Sequence
            key={`chapter-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            <ChapterTitleCard
              title={element.text}
              chapterNumber={index + 1}
            />
          </Sequence>
        );
      })}

      {/* Quote overlays */}
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
            <LongformQuote
              text={element.text}
              attribution={element.attribution}
            />
          </Sequence>
        );
      })}

      {/* Narration text */}
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
            <LongformNarration text={element.text} />
          </Sequence>
        );
      })}

      {/* Captions */}
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
            <LongformCaption text={element.text} />
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
              volume={isMusic ? 0.12 : 1}
            />
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

// --- Sub-components ---

const LongformIntro: React.FC<{
  title: string;
  watermark: string;
}> = ({ title, watermark }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 12,
  });

  const opacity = interpolate(enter, [0, 1], [0, 1]);
  const scaleVal = interpolate(enter, [0, 1], [0.92, 1]);

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
            fontSize: 22,
            color: GOLD,
            fontFamily: "Georgia, serif",
            letterSpacing: 5,
            textTransform: "uppercase",
          }}
        >
          {watermark}
        </div>
        <div
          style={{
            fontSize: 80,
            lineHeight: "92px",
            color: "white",
            fontFamily: "Georgia, serif",
            textAlign: "center",
            maxWidth: "75%",
            textShadow: "0 3px 16px rgba(0,0,0,0.9)",
          }}
        >
          {title}
        </div>
        <div
          style={{
            width: 160,
            height: 3,
            backgroundColor: GOLD,
            marginTop: 12,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};

const ChapterTitleCard: React.FC<{
  title: string;
  chapterNumber: number;
}> = ({ title, chapterNumber }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 10,
  });

  const opacity = interpolate(enter, [0, 1], [0, 1]);
  const translateY = interpolate(enter, [0, 1], [40, 0]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 25,
        justifyContent: "center",
        alignItems: "center",
        backgroundColor: "rgba(0, 0, 0, 0.8)",
      }}
    >
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 20,
          opacity,
          transform: `translateY(${translateY}px)`,
        }}
      >
        <div
          style={{
            fontSize: 24,
            color: GOLD,
            fontFamily: "Georgia, serif",
            letterSpacing: 6,
            textTransform: "uppercase",
          }}
        >
          Chapter {chapterNumber}
        </div>
        <div
          style={{
            width: 80,
            height: 2,
            backgroundColor: GOLD,
          }}
        />
        <div
          style={{
            fontSize: 60,
            lineHeight: "72px",
            color: "white",
            fontFamily: "Georgia, serif",
            textAlign: "center",
            maxWidth: "70%",
            marginTop: 10,
            textShadow: "0 2px 12px rgba(0,0,0,0.8)",
          }}
        >
          {title}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const LongformQuote: React.FC<{
  text: string;
  attribution?: string;
}> = ({ text, attribution }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 10,
  });

  const opacity = interpolate(enter, [0, 1], [0, 1]);
  const translateY = interpolate(enter, [0, 1], [25, 0]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 15,
        justifyContent: "center",
        alignItems: "center",
        padding: "0 140px",
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(0, 0, 0, 0.5)",
          borderRadius: 16,
          padding: "48px 56px",
          maxWidth: "80%",
          opacity,
          transform: `translateY(${translateY}px)`,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 20,
        }}
      >
        <div
          style={{
            fontSize: 50,
            lineHeight: "68px",
            color: "white",
            fontFamily: "Georgia, serif",
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
              fontSize: 30,
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

const LongformNarration: React.FC<{ text: string }> = ({ text }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 8,
  });

  const opacity = interpolate(enter, [0, 1], [0, 1]);

  return (
    <AbsoluteFill
      style={{
        zIndex: 12,
        justifyContent: "center",
        alignItems: "center",
        padding: "0 220px",
      }}
    >
      <div
        style={{
          fontSize: 36,
          lineHeight: "52px",
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

const LongformCaption: React.FC<{ text: string }> = ({ text }) => {
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
          textAlign: "center",
          textTransform: "uppercase",
          WebkitTextStroke: "1.5px rgba(0,0,0,0.7)",
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
