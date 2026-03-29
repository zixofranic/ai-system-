import { Composition, getStaticFiles } from "remotion";
import { AIVideo, aiVideoSchema } from "./components/AIVideo";
import { LongformVideo, longformVideoSchema } from "./components/LongformVideo";
import { MidformVideo, midformVideoSchema } from "./components/MidformVideo";
import { ShortVideo, shortVideoSchema } from "./components/ShortVideo";
import { FPS, INTRO_DURATION } from "./lib/constants";
import { getTimelinePath, loadMetadata, loadTimelineFromFile } from "./lib/utils";

const FORMAT_COMPONENTS = {
  story: { component: AIVideo, schema: aiVideoSchema },
  short: { component: ShortVideo, schema: shortVideoSchema },
  midform: { component: MidformVideo, schema: midformVideoSchema },
  longform: { component: LongformVideo, schema: longformVideoSchema },
} as const;

type FormatKey = keyof typeof FORMAT_COMPONENTS;

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
          component={AIVideo}
          fps={FPS}
          width={1920}
          height={1080}
          schema={aiVideoSchema}
          defaultProps={{
            timeline: null,
          }}
          calculateMetadata={async ({ props }) => {
            const { lengthFrames, timeline } = await loadTimelineFromFile(
              getTimelinePath(name),
            );
            const meta = await loadMetadata(name);
            const format = (meta?.format || "story") as FormatKey;
            const entry = FORMAT_COMPONENTS[format] || FORMAT_COMPONENTS.story;

            // Shorts have no intro title card (handled inside component)
            const introFrames = format === "short" ? 0 : INTRO_DURATION;

            return {
              durationInFrames: lengthFrames + introFrames,
              width: meta?.width || 1920,
              height: meta?.height || 1080,
              component: entry.component,
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
