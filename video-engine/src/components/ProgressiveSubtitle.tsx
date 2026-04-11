import { makeTransform, scale, translateY } from "@remotion/animation-utils";
import { loadFont } from "@remotion/google-fonts/BreeSerif";
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
 * Renders the FULL sentence text (with stable layout / fixed font size),
 * but each word fades in at its Whisper timestamp. Already-spoken words
 * stay on screen until the sentence's Sequence ends, giving the viewer a
 * stable visual anchor instead of a single flickering word.
 *
 * Two stacked layers preserve the Hormozi-style thick stroke + clean fill.
 */
const ProgressiveSubtitle: React.FC<{
  text: string;
  words: WordTiming[];
  sentenceStartMs: number;
}> = ({ text, words, sentenceStartMs }) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();
  const { fontFamily } = loadFont();
  const desiredFontSize = 110;

  // Fit the FULL sentence ONCE so the layout never shifts as words appear.
  const fittedText = fitText({
    fontFamily,
    text,
    withinWidth: width * 0.82,
  });
  const fontSize = Math.min(desiredFontSize, fittedText.fontSize);

  // Local time within this sentence's Sequence (frame is sequence-relative)
  const localMs = (frame / fps) * 1000;

  // Subtle entrance animation for the whole sentence box (gives a beat
  // when the sentence appears, instead of just popping)
  const enter = spring({
    frame,
    fps,
    config: { damping: 200 },
    durationInFrames: 6,
  });

  const renderSpans = () =>
    words.map((w, i) => {
      const relStartMs = w.startMs - sentenceStartMs;
      const fadeMs = 90;
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
    bottom: 80,
    height: 240,
  };

  const sharedTextStyle: React.CSSProperties = {
    fontSize,
    transform: makeTransform([
      scale(interpolate(enter, [0, 1], [0.94, 1])),
      translateY(interpolate(enter, [0, 1], [24, 0])),
    ]),
    fontFamily,
    textTransform: "uppercase",
    textAlign: "center",
    lineHeight: 1.1,
    maxWidth: width * 0.85,
  };

  // Suppress unused-warning: text is used to size the layout via fitText
  void text;

  return (
    <>
      {/* Stroke layer */}
      <AbsoluteFill style={containerStyle}>
        <div
          style={{
            ...sharedTextStyle,
            color: "white",
            WebkitTextStroke: "20px black",
          }}
        >
          {renderSpans()}
        </div>
      </AbsoluteFill>
      {/* Fill layer (sits on top of the stroke for the bold filled-letter look) */}
      <AbsoluteFill style={containerStyle}>
        <div
          style={{
            ...sharedTextStyle,
            color: "white",
          }}
        >
          {renderSpans()}
        </div>
      </AbsoluteFill>
    </>
  );
};

export default ProgressiveSubtitle;
