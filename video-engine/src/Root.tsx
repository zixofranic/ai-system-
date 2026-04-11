import { Composition, getStaticFiles } from "remotion";
import { AIVideo, aiVideoSchema } from "./components/AIVideo";
import { LongformVideo } from "./components/LongformVideo";
import { MidformVideo } from "./components/MidformVideo";
import { ShortVideo } from "./components/ShortVideo";
import { StoryVerticalVideo } from "./components/StoryVerticalVideo";
import { FPS, INTRO_DURATION } from "./lib/constants";
import type { Timeline } from "./lib/types";
import { getTimelinePath, loadMetadata, loadTimelineFromFile } from "./lib/utils";
import { z } from "zod";
import { TimelineSchema } from "./lib/types";

const routerSchema = z.object({
  timeline: TimelineSchema.nullable(),
});

/**
 * Routes to the correct video component based on timeline.metadata.format.
 * This avoids relying on calculateMetadata to override the component,
 * which doesn't work reliably in all Remotion versions.
 */
const FormatRouter: React.FC<z.infer<typeof routerSchema>> = ({ timeline }) => {
  if (!timeline) throw new Error("Expected timeline to be fetched");

  const format = timeline.metadata?.format || "story";

  switch (format) {
    case "short":
      return <ShortVideo timeline={timeline} />;
    case "story_vertical":
      return <StoryVerticalVideo timeline={timeline} />;
    case "midform":
      return <MidformVideo timeline={timeline} />;
    case "longform":
      return <LongformVideo timeline={timeline} />;
    default:
      return <AIVideo timeline={timeline} />;
  }
};

export const RemotionRoot: React.FC = () => {
  const staticFiles = getStaticFiles();
  const compositions = staticFiles
    .filter((file) => file.name.endsWith("timeline.json"))
    .map((file) => file.name.split("/")[1]);

  return (
    <>
      {compositions.map((name) => (
        <Composition
          id={name}
          key={name}
          component={FormatRouter}
          fps={FPS}
          width={1920}
          height={1080}
          schema={routerSchema}
          defaultProps={{
            timeline: null,
          }}
          calculateMetadata={async ({ props }) => {
            const { lengthFrames, timeline } = await loadTimelineFromFile(
              getTimelinePath(name),
            );
            const meta = await loadMetadata(name);
            const format = meta?.format || "story";

            // Shorts and story_vertical skip the intro title card — they
            // live on the Shorts feed where every frame must earn attention.
            const introFrames =
              format === "short" || format === "story_vertical"
                ? 0
                : INTRO_DURATION;

            return {
              durationInFrames: lengthFrames + introFrames,
              width: meta?.width || 1920,
              height: meta?.height || 1080,
              props: {
                ...props,
                timeline,
              },
            };
          }}
        />
      ))}
    </>
  );
};
