// Channel-aware metadata resolver. Maps a script's channel/philosopher into
// the slug + watermark label shown at the top of videos. Extend the WATERMARKS
// map as new channels are added.
//
// Preference order:
//   1. script.channel — explicit slug from the timeline input
//   2. Philosopher-based fallbacks (e.g. anything containing "gibran")
//   3. Default to wisdom
//
// The watermark must be a human-readable banner, not a slug.

const WATERMARKS = {
  gibran: "Gibran Khalil Gibran",
  wisdom: "Deep Echoes of Wisdom",
  na: "One Day At A Time",
  aa: "Easy Does It",
};

function resolveChannelMeta(script) {
  const rawChannel = (script.channel || "").toLowerCase();
  const rawPhilosopher = (script.philosopher || "").toLowerCase();

  // Explicit slug wins
  if (rawChannel && WATERMARKS[rawChannel]) {
    return { channel: rawChannel, watermark: WATERMARKS[rawChannel] };
  }

  // Philosopher-name fallback for legacy rows without channel slug
  if (rawPhilosopher.includes("gibran")) {
    return { channel: "gibran", watermark: WATERMARKS.gibran };
  }

  // If channel exists but isn't in the map, use the slug uppercased as a
  // last-resort label instead of falling through to Wisdom.
  if (rawChannel) {
    return {
      channel: rawChannel,
      watermark: rawChannel.toUpperCase(),
    };
  }

  // Default
  return { channel: "wisdom", watermark: WATERMARKS.wisdom };
}

module.exports = { resolveChannelMeta, WATERMARKS };
