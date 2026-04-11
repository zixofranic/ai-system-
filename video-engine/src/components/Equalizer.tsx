import React from "react";
import { useAudioData, visualizeAudio } from "@remotion/media-utils";
import { useCurrentFrame, useVideoConfig, staticFile } from "remotion";

interface EqualizerProps {
  /** Path to the audio file to visualize (typically the voice track). */
  audioSrc: string;
  /** Bar color (CSS hex / rgb). */
  color?: string;
  /** Number of bars to render. */
  numberOfBars?: number;
  /** Maximum bar height in pixels. */
  maxBarHeight?: number;
  /** Width of each bar in pixels. */
  barWidth?: number;
  /** Gap between bars in pixels. */
  gap?: number;
}

/**
 * Audio-reactive equalizer bars driven by Remotion's visualizeAudio.
 *
 * Renders a row of vertical bars whose heights pulse with the voice
 * track's frequency content. Returns null while the audio is loading
 * so the first few frames are clean rather than flashing.
 */
export const Equalizer: React.FC<EqualizerProps> = ({
  audioSrc,
  color = "#D4AF37",
  numberOfBars = 32,
  maxBarHeight = 120,
  barWidth = 6,
  gap = 4,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const audioData = useAudioData(audioSrc);

  if (!audioData) {
    return null;
  }

  // numberOfSamples must be a power of two (64, 128, 256, ...)
  // We only need HALF the bars' worth of FFT bins because we mirror them
  // to put bass in the middle and higher frequencies on both sides.
  const halfBars = Math.ceil(numberOfBars / 2);
  const samplesNeeded = Math.max(64, halfBars * 2);
  const numberOfSamples = Math.pow(2, Math.ceil(Math.log2(samplesNeeded)));

  const visualization = visualizeAudio({
    fps,
    frame,
    audioData,
    numberOfSamples,
    optimizeFor: "speed",
  });

  // Mirror the low-frequency half so bass sits in the center of the bar row
  // and higher frequencies fan out symmetrically to both sides. This avoids
  // the "all activity on the left" look of a raw FFT visualization where the
  // right half is always flat (human voice energy is mostly below 1 kHz).
  const lowHalf = visualization.slice(0, halfBars);
  const bars =
    numberOfBars % 2 === 0
      ? [...lowHalf.slice().reverse(), ...lowHalf]
      : [...lowHalf.slice(1).reverse(), ...lowHalf];

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "row",
        alignItems: "flex-end",
        justifyContent: "center",
        gap: `${gap}px`,
        height: `${maxBarHeight}px`,
        width: "100%",
      }}
    >
      {bars.map((value, i) => {
        // Boost low values so quiet sections still show some motion.
        const normalized = Math.min(1, Math.pow(value, 0.6) * 2.2);
        const height = Math.max(4, normalized * maxBarHeight);
        return (
          <div
            key={i}
            style={{
              width: `${barWidth}px`,
              height: `${height}px`,
              backgroundColor: color,
              borderRadius: `${barWidth / 2}px`,
              boxShadow: `0 0 ${barWidth}px ${color}80`,
            }}
          />
        );
      })}
    </div>
  );
};
