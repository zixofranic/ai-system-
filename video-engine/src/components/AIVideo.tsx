import { loadFont as loadEBGaramond } from "@remotion/google-fonts/EBGaramond";
import { Audio } from "@remotion/media";
import { AbsoluteFill, Sequence, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { z } from "zod";
import {
  FPS,
  GOLD,
  INTRO_DURATION,
  MUSIC_FADE_FRAMES,
  MUSIC_VOLUME,
} from "../lib/constants";
import { TimelineSchema } from "../lib/types";
import { calculateFrameTiming, getAudioPath } from "../lib/utils";
import { Background } from "./Background";
import { Equalizer } from "./Equalizer";
import ProgressiveSubtitle from "./ProgressiveSubtitle";
import Subtitle from "./Subtitle";

export const aiVideoSchema = z.object({
  timeline: TimelineSchema.nullable(),
});

const ebGaramond = loadEBGaramond();

// Cinematic intro card — used when timeline.metadata.theme.cinematic === true.
// Adopts the structural pattern from MidformIntro (uppercase letter-spaced
// watermark + accent divider) but renders in the cinematic palette: EB
// Garamond italic title, aged-paper color, amber-red divider — same design
// system as the cinematic captions and equalizer.
// 2026-04-20: shifted accent from amber #D49A45 -> amber-red #C2603C
// (terracotta / burnt sienna) per Ziad — warmer, earthier, more Levantine.
const INTRO_AMBER = "#C2603C";
const INTRO_TEXT = "#C9B888";

// Per-channel accent color for intro divider + watermark + persistent
// watermark. Defaults to the Gibran amber-red when channel is unknown.
// Keep in sync with orchestrator.CHANNEL_DEFAULT_EQ.
const CHANNEL_ACCENT: Record<string, string> = {
  wisdom: "#D4AF37",  // warm gold
  gibran: "#C2603C",  // terracotta / burnt sienna (Levantine)
  na: "#4F8FB8",      // calm blue — clear water
  aa: "#7A9E7E",      // soft sage — grounding green
};
function accentForChannel(channel: string | null | undefined): string {
  if (!channel) return INTRO_AMBER;
  return CHANNEL_ACCENT[channel.toLowerCase()] || INTRO_AMBER;
}

const CinematicIntro: React.FC<{
  title: string;
  watermark: string;
  accent?: string;
}> = ({ title, watermark, accent = INTRO_AMBER }) => {
  const frame = useCurrentFrame();
  const watermarkFade = interpolate(frame, [0, 15], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const titleFade = interpolate(frame, [8, 26], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const dividerFade = interpolate(frame, [16, 30], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const dividerWidth = interpolate(frame, [16, 36], [0, 140], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        textAlign: "center",
        display: "flex",
        zIndex: 10,
        backgroundColor: "black",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
        <div style={{
          fontSize: 24,
          color: accent,
          fontFamily: ebGaramond.fontFamily,
          letterSpacing: 5,
          textTransform: "uppercase",
          opacity: watermarkFade,
        }}>
          {watermark}
        </div>
        <div style={{
          fontSize: 78,
          lineHeight: 1.18,
          maxWidth: 1400,
          color: INTRO_TEXT,
          fontFamily: ebGaramond.fontFamily,
          fontStyle: "italic",
          letterSpacing: 0.5,
          opacity: titleFade,
          textShadow: "0 2px 12px rgba(0,0,0,0.7)",
        }}>
          {title}
        </div>
        <div style={{
          width: dividerWidth,
          height: 2,
          backgroundColor: accent,
          marginTop: 6,
          opacity: dividerFade,
        }} />
      </div>
    </AbsoluteFill>
  );
};

// Persistent top watermark — only shown when cinematic. After the intro
// card fades, the channel name lives quietly at the top of every scene
// so the viewer always knows whose voice they're hearing. Low-opacity
// EB Garamond uppercase letter-spaced amber, matches the intro's
// design language without competing with the captions or the art.
const PersistentWatermark: React.FC<{
  watermark: string;
  accent?: string;
}> = ({ watermark, accent = INTRO_AMBER }) => (
  <AbsoluteFill
    style={{
      zIndex: 6,            // above the art (0), below the intro card (10)
      justifyContent: "flex-start",
      alignItems: "center",
      paddingTop: 50,
      pointerEvents: "none",
    }}
  >
    <div
      style={{
        fontSize: 22,
        color: accent,
        fontFamily: ebGaramond.fontFamily,
        letterSpacing: 5,
        textTransform: "uppercase",
        opacity: 0.7,
        textShadow: "0 1px 6px rgba(0,0,0,0.7)",
      }}
    >
      {watermark}
    </div>
  </AbsoluteFill>
);


// Default brutalist intro — preserved for non-cinematic story renders so
// the existing Wisdom/NA story output doesn't regress. Yellow + black
// border was original; kept as-is.
const DefaultIntro: React.FC<{ title: string }> = ({ title }) => (
  <AbsoluteFill
    style={{
      justifyContent: "center",
      alignItems: "center",
      textAlign: "center",
      display: "flex",
      zIndex: 10,
    }}
  >
    <div style={{
      fontSize: 120,
      lineHeight: "122px",
      width: "87%",
      color: "black",
      fontFamily: ebGaramond.fontFamily,
      textTransform: "uppercase",
      backgroundColor: "yellow",
      paddingTop: 20,
      paddingBottom: 20,
      border: "10px solid black",
    }}>
      {title}
    </div>
  </AbsoluteFill>
);

export const AIVideo: React.FC<z.infer<typeof aiVideoSchema>> = ({
  timeline,
}) => {
  if (!timeline) {
    throw new Error("Expected timeline to be fetched");
  }

  const { id } = useVideoConfig();
  const cinematic = !!timeline.metadata?.theme?.cinematic;
  const watermark = timeline.metadata?.watermark || "Deep Echoes of Wisdom";
  const accent = accentForChannel(timeline.metadata?.channel);

  // Backplate: black for cinematic so fade-to-black between scenes reads
  // as a beat; white for default (the original behavior — preserved so
  // existing Wisdom/NA story renders look as they did before 2026-04-19).
  const backgroundColor = cinematic ? "black" : "white";

  return (
    <AbsoluteFill style={{ backgroundColor }}>
      <Sequence durationInFrames={INTRO_DURATION}>
        {cinematic ? (
          <CinematicIntro title={timeline.shortTitle ?? ""} watermark={watermark} accent={accent} />
        ) : (
          <DefaultIntro title={timeline.shortTitle ?? ""} />
        )}
      </Sequence>

      {/* Persistent top watermark — cinematic-only. Lives outside the
          intro Sequence so it's visible for every scene after the intro
          fades. The intro card has zIndex:10 and covers it during the
          first INTRO_DURATION frames, then it shows through. */}
      {cinematic && <PersistentWatermark watermark={watermark} accent={accent} />}

      {timeline.elements.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { includeIntro: index === 0 },
        );

        return (
          <Sequence
            key={`element-${index}`}
            from={startFrame}
            durationInFrames={duration}
            premountFor={3 * FPS}
          >
            <Background project={id} item={element} />
          </Sequence>
        );
      })}

      {timeline.text.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { addIntroOffset: true },
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
                cinematic={cinematic}
              />
            ) : (
              <Subtitle text={element.text} cinematic={cinematic} />
            )}
          </Sequence>
        );
      })}

      {timeline.audio.map((element, index) => {
        const { startFrame, duration } = calculateFrameTiming(
          element.startMs,
          element.endMs,
          { addIntroOffset: true },
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

      {/* Equalizer — only rendered for cinematic theme. The default story
          look (Wisdom/NA) historically had no equalizer; preserve that. */}
      {cinematic && timeline.audio
        .filter((el) => el.audioUrl !== "music")
        .slice(0, 1)
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
              premountFor={3 * FPS}
            >
              <AbsoluteFill
                style={{
                  zIndex: 5,
                  justifyContent: "flex-end",
                  alignItems: "center",
                  paddingBottom: 80,
                  pointerEvents: "none",
                }}
              >
                <Equalizer
                  audioSrc={staticFile(getAudioPath(id, element.audioUrl))}
                  color={timeline.metadata?.equalizerColor || GOLD}
                  numberOfBars={44}
                  maxBarHeight={110}
                  barWidth={5}
                  gap={6}
                />
              </AbsoluteFill>
            </Sequence>
          );
        })}
    </AbsoluteFill>
  );
};
