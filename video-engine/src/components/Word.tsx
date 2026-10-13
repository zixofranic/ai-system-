import { makeTransform, scale, translateY } from "@remotion/animation-utils";
import { loadFont as loadBreeSerif } from "@remotion/google-fonts/BreeSerif";
import { loadFont as loadEBGaramond } from "@remotion/google-fonts/EBGaramond";
import { fitText } from "@remotion/layout-utils";
import type React from "react";
import { AbsoluteFill, interpolate, useVideoConfig } from "remotion";

// Two visual modes — picked at render time via the optional `cinematic`
// flag (timeline.metadata.theme.cinematic).
//
// DEFAULT (Hormozi style) — original Wisdom / NA / AA look:
//   BreeSerif uppercase, white fill, 20px black stroke, no shadow.
//   Algorithm-friendly: high contrast, readable on phone screens at small
//   size, the look that drives Shorts views.
//
// CINEMATIC — opt-in for Gibran longform essays:
//   EBGaramond italic, aged-paper #C9B888, thin 3px stroke + drop shadow.
//   Reads as literary / film essay; better for sit-down 16:9 viewing.
const HORMOZI = {
  color: "white" as const,
  strokePx: 20,
  strokeColor: "black" as const,
  textShadow: undefined as string | undefined,
  fontStyle: "normal" as const,
  fontWeight: 400 as const,
  letterSpacing: 0,
  textTransform: "uppercase" as const,
  desiredFontSize: 120,
  enterScaleFrom: 0.8,
  enterTranslateFromPx: 50,
};

const CINEMATIC = {
  color: "#C9B888" as const,
  strokePx: 3,
  strokeColor: "rgba(0,0,0,0.85)" as const,
  textShadow: "0 2px 14px rgba(0,0,0,0.85)" as string | undefined,
  fontStyle: "italic" as const,
  fontWeight: 500 as const,
  letterSpacing: 0.5,
  textTransform: "none" as const,
  desiredFontSize: 80,
  enterScaleFrom: 0.92,
  enterTranslateFromPx: 24,
};

// Pre-load both fonts at module scope so neither blocks first render.
const breeSerif = loadBreeSerif();
const ebGaramond = loadEBGaramond();

export const Word: React.FC<{
  enterProgress: number;
  text: string;
  stroke: boolean;
  cinematic?: boolean;
}> = ({ enterProgress, text, stroke, cinematic = false }) => {
  const theme = cinematic ? CINEMATIC : HORMOZI;
  const fontFamily = cinematic ? ebGaramond.fontFamily : breeSerif.fontFamily;
  const { width } = useVideoConfig();

  const fittedText = fitText({
    fontFamily,
    text,
    withinWidth: width * 0.8,
  });
  const fontSize = Math.min(theme.desiredFontSize, fittedText.fontSize);

  return (
    <AbsoluteFill
      style={{
        justifyContent: "center",
        alignItems: "center",
        top: undefined,
        bottom: 80,
        height: 150,
      }}
    >
      <div
        style={{
          fontSize,
          color: theme.color,
          WebkitTextStroke: stroke
            ? `${theme.strokePx}px ${theme.strokeColor}`
            : undefined,
          // textShadow is a pure-cinematic touch (Hormozi look has no shadow)
          textShadow: stroke ? undefined : theme.textShadow,
          transform: makeTransform([
            scale(interpolate(enterProgress, [0, 1], [theme.enterScaleFrom, 1])),
            translateY(interpolate(enterProgress, [0, 1], [theme.enterTranslateFromPx, 0])),
          ]),
          fontFamily,
          fontStyle: theme.fontStyle,
          fontWeight: theme.fontWeight,
          letterSpacing: theme.letterSpacing,
          textTransform: theme.textTransform,
          textAlign: "center",
          lineHeight: cinematic ? 1.18 : 1.05,
        }}
      >
        {text}
      </div>
    </AbsoluteFill>
  );
};
