import {
  AbsoluteFill,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { FPS, IMAGE_HEIGHT, IMAGE_WIDTH } from "../lib/constants";
import { BackgroundElement } from "../lib/types";
import { calculateBlur, getImagePath } from "../lib/utils";

const EXTRA_SCALE = 0.2;

export const Background: React.FC<{
  item: BackgroundElement;
  project: string;
}> = ({ item, project }) => {
  const frame = useCurrentFrame();
  const localMs = (frame / FPS) * 1000;
  const { width, height } = useVideoConfig();

  // Scale image to cover the full frame (landscape)
  const coverScale = Math.max(width / IMAGE_WIDTH, height / IMAGE_HEIGHT);
  const imgWidth = IMAGE_WIDTH * coverScale;
  const imgHeight = IMAGE_HEIGHT * coverScale;
  let animScale = 1 + EXTRA_SCALE;

  // 2026-04-20 fix: pick the scale animation regardless of whether
  // localMs is currently inside its window, then CLAMP progress to [0,1].
  // The previous version only applied the animation when start<=localMs<=end,
  // so the moment localMs ticked past `end` the scale snapped from its
  // end value (~1.26 for even scenes) back to the default 1.2 — visible
  // as a pop in the last few frames before the crossfade. Clamping holds
  // the end value through the fade-out so the cut is clean.
  const scaleAnim = item.animations?.find((anim) => anim.type === "scale");
  if (scaleAnim) {
    const span = Math.max(1, scaleAnim.endMs - scaleAnim.startMs);
    const progress = Math.min(1, Math.max(0, (localMs - scaleAnim.startMs) / span));
    animScale =
      EXTRA_SCALE +
      progress * (scaleAnim.to - scaleAnim.from) +
      scaleAnim.from;
  }

  const imgScale = animScale;
  const top = -(imgHeight * imgScale - height) / 2;
  const left = -(imgWidth * imgScale - width) / 2;

  const blur = calculateBlur({ item, localMs });
  const maxBlur = 25;

  const currentBlur = maxBlur * blur;

  // Soft fade in/out over 1 second
  const { durationInFrames } = useVideoConfig();
  const fadeFrames = Math.min(FPS, Math.floor(durationInFrames / 4));
  const opacity = interpolate(
    frame,
    [0, fadeFrames, durationInFrames - fadeFrames, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );

  return (
    <AbsoluteFill style={{ opacity }}>
      <Img
        src={staticFile(getImagePath(project, item.imageUrl))}
        style={{
          width: imgWidth * imgScale,
          height: imgHeight * imgScale,
          position: "absolute",
          top,
          left,
          filter: `blur(${currentBlur}px)`,
          WebkitFilter: `blur(${currentBlur}px)`,
        }}
      />
    </AbsoluteFill>
  );
};
