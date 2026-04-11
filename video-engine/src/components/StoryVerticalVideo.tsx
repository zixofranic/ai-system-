import { Audio } from "@remotion/media";
import {
  AbsoluteFill,
  Img,
  Sequence,
  interpolate,
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
import type { BackgroundElement } from "../lib/types";
import { calculateFrameTiming, getAudioPath, getImagePath } from "../lib/utils";
import { Equalizer } from "./Equalizer";
import ProgressiveSubtitle from "./ProgressiveSubtitle";
import Subtitle from "./Subtitle";

export const storyVerticalVideoSchema = z.object({
  timeline: TimelineSchema.nullable(),
});

/**
 * StoryVerticalVideo — 9:16 portrait composition (1080x1920) for condensed
 * ~60-second story teasers. Companion format to the full horizontal Story.
 *
 * Visual layout:
 *   - Background: landscape story art cropped with pan-and-scan (alternates
 *     left-to-right / right-to-left per scene for visual rhythm)
 *   - Top: small "Deep Echoes of Wisdom" watermark
 *   - Middle/upper: ProgressiveSubtitle captions building sentence-by-sentence
 *   - Bottom: gold equalizer bars
 *   - Voice (James Burton) + background music with fade in/out
 */
export const StoryVerticalVideo: React.FC<
  z.infer<typeof storyVerticalVideoSchema>
> = ({ timeline }) => {
  if (!timeline) throw new Error("Expected timeline to be fetched");

  const { id } = useVideoConfig();
  const watermark = timeline.metadata?.watermark || "Deep Echoes of Wisdom";

  return (
    <AbsoluteFill style={{ backgroundColor: "black" }}>
      {/* Pan-and-scan background scenes */}
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
            <PanScanBackground
              project={id}
              item={element}
              panDirection={index % 2 === 0 ? "leftToRight" : "rightToLeft"}
            />
          </Sequence>
        );
      })}

      {/* Top + bottom gradients for text readability */}
      <AbsoluteFill style={{ background: DARK_GRADIENT_TOP, zIndex: 5 }} />
      <AbsoluteFill style={{ background: DARK_GRADIENT_BOTTOM, zIndex: 5 }} />

      {/* Top watermark */}
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
            color: "rgba(255,255,255,0.75)",
            fontFamily: "Georgia, serif",
            letterSpacing: 3,
            textTransform: "uppercase",
          }}
        >
          {watermark}
        </div>
      </AbsoluteFill>

      {/* Progressive sentence-build captions (same style as horizontal stories) */}
      {timeline.text.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
        );
        return (
          <Sequence
            key={`text-${index}`}
            from={startFrame}
            durationInFrames={duration}
          >
            {element.words && element.words.length > 0 ? (
              <ProgressiveSubtitle
                text={element.text}
                words={element.words}
                sentenceStartMs={element.startMs}
              />
            ) : (
              <Subtitle text={element.text} />
            )}
          </Sequence>
        );
      })}

      {/* Audio (voice + music with fade) */}
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

      {/* Equalizer — pinned above the caption zone (captions sit at
          bottom:80 + 150 height = 230px from bottom, so equalizer must
          start well above that to avoid visual collision). */}
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
                  paddingBottom: 360,
                  pointerEvents: "none",
                }}
              >
                <Equalizer
                  audioSrc={staticFile(getAudioPath(id, element.audioUrl))}
                  color={timeline.metadata?.equalizerColor || GOLD}
                  numberOfBars={36}
                  maxBarHeight={80}
                  barWidth={6}
                  gap={5}
                />
              </AbsoluteFill>
            </Sequence>
          );
        })}
    </AbsoluteFill>
  );
};

// ---------------------------------------------------------------------------
// PanScanBackground — crops a landscape image into a 9:16 vertical window
// and slides that window horizontally across the image over the scene
// duration. Source-aspect-agnostic (uses objectFit:cover).
// ---------------------------------------------------------------------------
const PanScanBackground: React.FC<{
  item: BackgroundElement;
  project: string;
  panDirection: "leftToRight" | "rightToLeft";
}> = ({ item, project, panDirection }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  // Pre-enlarge the image to 140% of the canvas width so we have 20% of
  // overflow on each side. Horizontal pan of ±10% (of the image's OWN
  // 140%-width, i.e. ±14% of canvas) stays well within the overflow margin,
  // so we never expose a black edge regardless of pan direction.
  const PAN_RANGE_PCT = 10;

  const pan = interpolate(
    frame,
    [0, durationInFrames],
    panDirection === "leftToRight"
      ? [-PAN_RANGE_PCT, PAN_RANGE_PCT]
      : [PAN_RANGE_PCT, -PAN_RANGE_PCT],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Subtle Ken Burns zoom over the scene
  const zoom = interpolate(
    frame,
    [0, durationInFrames],
    panDirection === "leftToRight" ? [1.05, 1.12] : [1.12, 1.05],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  // Fade in/out
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
          width: "140%",
          height: "100%",
          objectFit: "cover",
          position: "absolute",
          left: "-20%",
          top: 0,
          transform: `translateX(${pan}%) scale(${zoom})`,
          transformOrigin: "center center",
        }}
      />
    </AbsoluteFill>
  );
};
