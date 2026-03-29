import { Composition, getStaticFiles } from "remotion";
import { AIVideo, aiVideoSchema } from "./components/AIVideo";
import { FPS, INTRO_DURATION } from "./lib/constants";
import { getTimelinePath, loadTimelineFromFile, loadMetadata } from "./lib/utils";

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

            return {
              durationInFrames: lengthFrames + INTRO_DURATION,
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
