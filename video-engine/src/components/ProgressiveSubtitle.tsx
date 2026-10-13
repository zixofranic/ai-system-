import { makeTransform, scale, translateY } from "@remotion/animation-utils";
import { loadFont as loadBreeSerif } from "@remotion/google-fonts/BreeSerif";
import { loadFont as loadEBGaramond } from "@remotion/google-fonts/EBGaramond";
import { fitText } from "@remotion/layout-utils";
import type React from "react";
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type { WordTiming } from "../lib/types";

/**
 * ProgressiveSubtitle — sentence-build captions.
 *
 * Renders the FULL sentence with stable layout, but each word fades in at
 * its Whisper timestamp. Already-spoken words stay on screen until the
 * sentence's Sequence ends — a stable visual anchor instead of a single
 * flickering word.
 *
 * Two visual modes (picked by `cinematic` prop, defaulting to false):
 *   DEFAULT (Hormozi)  — BreeSerif uppercase, white on 20px black stroke,
 *                        bottom: 80, height: 240. The look that drives
 *                        Shorts views; algorithm-friendly.
 *   CINEMATIC (Gibran) — EBGaramond italic, aged-paper #C9B888, thin 3px
 *                        stroke + drop shadow, bottom: 260 to clear the
 *                        AIVideo equalizer band. Literary / film essay feel.
 */
const HORMOZI = {
  color: "white" as const,
  strokePx: 20,
  strokeColor: "black" as const,
  textShadowFill: undefined as string | undefined,
  fontStyle: "normal" as const,
  fontWeight: 400 as const,
  letterSpacing: 0,
  textTransform: "uppercase" as const,
  desiredFontSize: 110,
  enterScaleFrom: 0.94,
  enterTranslateFromPx: 24,
  bottomPx: 80,
  lineHeight: 1.1,
};

const CINEMATIC = {
  color: "#C9B888" as const,
  strokePx: 3,
  strokeColor: "rgba(0,0,0,0.85)" as const,
  textShadowFill: "0 2px 14px rgba(0,0,0,0.85)" as string | undefined,
  fontStyle: "italic" as const,
  fontWeight: 500 as const,
  letterSpacing: 0.5,
  textTransform: "none" as const,
  desiredFontSize: 78,
  enterScaleFrom: 0.96,
  enterTranslateFromPx: 18,
  // Cleared above the AIVideo equalizer band (paddingBottom 80 +
  // maxBarHeight 110 -> top of bars at ~190px; bottom:260 leaves ~70px
  // breathing room).
  bottomPx: 260,
  lineHeight: 1.22,
};

const breeSerif = loadBreeSerif();
const ebGaramond = loadEBGaramond();

const ProgressiveSubtitle: React.FC<{
  text: string;
  words: WordTiming[];
  sentenceStartMs: number;
  cinematic?: boolean;
}> = ({ text, words, sentenceStartMs, cinematic = false }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  const theme = cinematic ? CINEMATIC : HORMOZI;
  const fontFamily = cinematic ? ebGaramond.fontFamily : breeSerif.fontFamily;

  // Fit the FULL sentence ONCE so the layout never shifts as words appear.
  const fittedText = fitText({
    fontFamily,
    text,
    withinWidth: width * 0.82,
  });
  const fontSize = Math.min(theme.desiredFontSize, fittedText.fontSize);

  // Local time within this sentence's Sequence (frame is sequence-relative)
  const localMs = (frame / fps) * 1000;

  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: cinematic ? 8 : 6,
  });

  const renderSpans = () =>
    words.map((w, i) => {
      const relStartMs = w.startMs - sentenceStartMs;
      const fadeMs = cinematic ? 110 : 90;
      const opacity = interpolate(
        localMs,
        [relStartMs, relStartMs + fadeMs],
        [0, 1],
        { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
      );
      return (
        <span key={i} style={{ opacity }}>
          {i > 0 ? " " : ""}
          {w.word}
        </span>
      );
    });

  const containerStyle: React.CSSProperties = {
    justifyContent: "center",
    alignItems: "center",
    top: undefined,
    bottom: theme.bottomPx,
    height: 240,
  };

  const sharedTextStyle: React.CSSProperties = {
    fontSize,
    transform: makeTransform([
      scale(interpolate(enter, [0, 1], [theme.enterScaleFrom, 1])),
      translateY(interpolate(enter, [0, 1], [theme.enterTranslateFromPx, 0])),
    ]),
    fontFamily,
    fontStyle: theme.fontStyle,
    fontWeight: theme.fontWeight,
    letterSpacing: theme.letterSpacing,
    textTransform: theme.textTransform,
    textAlign: "center",
    lineHeight: theme.lineHeight,
    maxWidth: width * 0.85,
  };

  // Suppress unused-warning: text is used to size the layout via fitText
  void text;

  return (
    <>
      {/* Stroke layer — outline for legibility on busy art */}
      <AbsoluteFill style={containerStyle}>
        <div
          style={{
            ...sharedTextStyle,
            color: theme.color,
            WebkitTextStroke: `${theme.strokePx}px ${theme.strokeColor}`,
          }}
        >
          {renderSpans()}
        </div>
      </AbsoluteFill>
      {/* Fill layer — sits on top of the stroke; cinematic adds a soft shadow */}
      <AbsoluteFill style={containerStyle}>
        <div
          style={{
            ...sharedTextStyle,
            color: theme.color,
            textShadow: theme.textShadowFill,
          }}
        >
          {renderSpans()}
        </div>
      </AbsoluteFill>
    </>
  );
};

export default ProgressiveSubtitle;
