"""
Thumbnail Generator — Creates YouTube/TikTok thumbnails from scene images.

Used by:
  - generate_story_video.py (stories)
  - orchestrator.py (shorts, midform)
  - Standalone: python thumbnail_generator.py --image scene.png --title "Title" --output thumb.jpg
"""

import os
import sys
import textwrap
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance


# Channels whose generated SDXL art is too risky for YouTube's automated
# thumbnail moderation get text-only thumbnails. Per-channel theming below.
CHANNEL_THUMB_THEMES = {
    "gibran": {
        "bg_top": (61, 24, 24),       # deep wine
        "bg_bottom": (10, 5, 5),      # near-black warm
        "accent": (212, 165, 116),    # warm gold
        "wordmark": "GIBRAN",
    },
    "wisdom": {
        "bg_top": (15, 23, 38),       # deep navy
        "bg_bottom": (4, 6, 12),      # near-black cool
        "accent": (212, 175, 55),     # gold
        "wordmark": "WISDOM",
    },
    "na": {
        "bg_top": (16, 36, 38),       # deep teal (matches Fellows app)
        "bg_bottom": (4, 10, 11),
        "accent": (232, 184, 104),    # warm milestone tone
        "wordmark": "ONE DAY AT A TIME",
    },
    "aa": {
        "bg_top": (16, 36, 38),
        "bg_bottom": (4, 10, 11),
        "accent": (232, 184, 104),
        "wordmark": "EASY DOES IT",
    },
}
DEFAULT_THUMB_THEME = {
    "bg_top": (20, 20, 30),
    "bg_bottom": (5, 5, 10),
    "accent": (200, 200, 200),
    "wordmark": None,
}


def generate_thumbnail(image_path, title, output_path, width=1920, height=1080):
    """
    Generate a thumbnail from a scene image with title text overlay.

    Args:
        image_path: Path to the source image (scene art)
        title: Video title text
        output_path: Where to save the thumbnail (.jpg)
        width: Output width (1920 for landscape, 1080 for portrait)
        height: Output height (1080 for landscape, 1920 for portrait)
    """
    img = Image.open(image_path).convert("RGB")
    img = img.resize((width, height), Image.LANCZOS)
    draw = ImageDraw.Draw(img)

    # Dark gradient at bottom (40% of image)
    gradient_start = int(height * 0.55)
    for y in range(gradient_start, height):
        progress = (y - gradient_start) / (height - gradient_start)
        alpha = int(230 * progress)
        draw.rectangle([0, y, width, y + 1], fill=(0, 0, 0, alpha))

    # Load font
    font_size = width // 20  # Scale with image width
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/georgiab.ttf", font_size)
    except OSError:
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/georgia.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()

    # Word wrap title
    max_chars = width // (font_size // 2)  # rough chars per line
    lines = textwrap.wrap(title, width=max_chars)
    if len(lines) > 3:
        lines = lines[:3]
        lines[-1] = lines[-1][:max_chars - 3] + "..."

    # Draw text with outline
    y_pos = int(height * 0.72)
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        text_w = bbox[2] - bbox[0]
        x = (width - text_w) // 2

        # Black outline
        outline_width = max(2, font_size // 25)
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx * dx + dy * dy <= outline_width * outline_width:
                    draw.text((x + dx, y_pos + dy), line, font=font, fill=(0, 0, 0))

        # White text
        draw.text((x, y_pos), line, font=font, fill=(255, 255, 255))
        y_pos += bbox[3] - bbox[1] + int(font_size * 0.2)

    img.save(output_path, "JPEG", quality=90)
    return output_path


def _load_font(paths, size):
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def generate_youtube_thumbnail(image_path, title, output_path,
                               channel_slug=None, width=1280, height=720):
    """Build a 16:9 (1280x720) YouTube thumbnail from PORTRAIT (9:16) art.

    YouTube custom thumbnails MUST be 16:9. Feeding thumbnails.set a 9:16
    portrait image returns HTTP 200 (it validates size/format/bytes, not
    aspect) but then FAILS YouTube's image-derivative pipeline — the result
    is a gray broken-image icon in Studio and a black poster on the channel
    grid (diagnosed 2026-06-02). The video stays 9:16; only THIS YouTube
    thumbnail is 16:9.

    Composition (keeps the dark-art channel aesthetic, no black pillars):
      - background: the portrait art scaled-to-cover the 16:9 frame, heavily
        blurred + darkened — fills the canvas in the channel's own palette
      - center panel: the un-blurred portrait art at full height
      - bottom gradient + title text wrapped to the 16:9 width
      - optional channel wordmark (accent) top-center
    """
    art = Image.open(image_path).convert("RGB")
    aw, ah = art.size

    # --- Background: cover-fill the 16:9 frame, blur, darken ---
    scale = max(width / aw, height / ah)
    bw, bh = max(1, int(aw * scale)), max(1, int(ah * scale))
    bg = art.resize((bw, bh), Image.LANCZOS)
    left, top = (bw - width) // 2, (bh - height) // 2
    bg = bg.crop((left, top, left + width, top + height))
    bg = bg.filter(ImageFilter.GaussianBlur(28))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    canvas = bg

    # --- Center panel: un-blurred portrait art at full height ---
    panel_w = max(1, int(height * (aw / ah)))
    panel = art.resize((panel_w, height), Image.LANCZOS)
    px = (width - panel_w) // 2
    canvas.paste(panel, (px, 0))
    # thin accent edges on the panel so it reads as a deliberate inset
    theme = CHANNEL_THUMB_THEMES.get((channel_slug or "").lower(),
                                     DEFAULT_THUMB_THEME)
    accent = theme["accent"]
    edge = ImageDraw.Draw(canvas)
    edge.line([(px, 0), (px, height)], fill=accent, width=3)
    edge.line([(px + panel_w, 0), (px + panel_w, height)], fill=accent, width=3)

    # --- Bottom gradient (proper alpha mask; the old per-row RGB alpha was
    #     a no-op) for title legibility across the full 16:9 width ---
    grad = Image.new("L", (1, height), 0)
    g_start = int(height * 0.46)
    for y in range(height):
        if y <= g_start:
            grad.putpixel((0, y), 0)
        else:
            t = (y - g_start) / max(1, height - g_start)
            grad.putpixel((0, y), int(225 * t))
    grad = grad.resize((width, height))
    canvas = Image.composite(Image.new("RGB", (width, height), (0, 0, 0)),
                             canvas, grad)
    draw = ImageDraw.Draw(canvas)

    # --- Channel wordmark (small, accent, top-center) ---
    wordmark = theme.get("wordmark")
    if wordmark:
        wm_font = _load_font([
            "C:/Windows/Fonts/georgiab.ttf", "C:/Windows/Fonts/georgia.ttf",
        ], 30)
        spaced = "  ".join(list(wordmark))
        wb = draw.textbbox((0, 0), spaced, font=wm_font)
        draw.text(((width - (wb[2] - wb[0])) // 2, 26), spaced,
                  font=wm_font, fill=accent)

    # --- Title: measure-based wrap to ~90% width, over the bottom gradient ---
    title_size = 72
    title_font = _load_font([
        "C:/Windows/Fonts/georgiabi.ttf", "C:/Windows/Fonts/georgiab.ttf",
        "C:/Windows/Fonts/georgia.ttf",
    ], title_size)
    target_w = int(width * 0.90)
    lines, current = [], ""
    for w in title.split():
        cand = (current + " " + w).strip()
        cb = draw.textbbox((0, 0), cand, font=title_font)
        if cb[2] - cb[0] <= target_w or not current:
            current = cand
        else:
            lines.append(current); current = w
    if current:
        lines.append(current)
    if len(lines) > 2:                       # 16:9 has little vertical room
        lines = lines[:2]
        lines[-1] = lines[-1].rstrip(" .,;:") + "..."

    lh = [draw.textbbox((0, 0), ln, font=title_font)[3] -
          draw.textbbox((0, 0), ln, font=title_font)[1] for ln in lines]
    spacing = int(title_size * 0.22)
    total_h = sum(lh) + spacing * max(0, len(lines) - 1)
    y = height - total_h - int(height * 0.07)

    outline_w = max(3, title_size // 18)
    for ln, h in zip(lines, lh):
        bb = draw.textbbox((0, 0), ln, font=title_font)
        x = (width - (bb[2] - bb[0])) // 2
        for dx in range(-outline_w, outline_w + 1):
            for dy in range(-outline_w, outline_w + 1):
                if dx * dx + dy * dy <= outline_w * outline_w:
                    draw.text((x + dx, y + dy), ln, font=title_font, fill=(0, 0, 0))
        draw.text((x, y), ln, font=title_font, fill=(255, 255, 255))
        y += h + spacing

    canvas.save(output_path, "JPEG", quality=90)
    return output_path


def generate_text_only_thumbnail(title, output_path, width=1080, height=1920,
                                 channel_slug=None):
    """Render a thumbnail with no source image — gradient background + title.

    For channels whose generated art is too risky for YouTube's automated
    thumbnail moderation. The Gibran channel had a thumbnail removed
    2026-05-07 for "sex and nudity" because the gibran_style_v1 LoRA is
    trained to produce classical-figural illuminated-manuscript imagery
    (the actual Gibran aesthetic). Text-only sidesteps the auto-moderator
    while leaving the in-video art untouched.
    """
    theme = CHANNEL_THUMB_THEMES.get((channel_slug or "").lower(),
                                     DEFAULT_THUMB_THEME)
    is_portrait = height > width

    # Vertical gradient via 1px-tall strip then resize — much faster than
    # per-row rectangles and produces a smoother result.
    strip = Image.new("RGB", (1, height))
    bt, bb = theme["bg_top"], theme["bg_bottom"]
    for y in range(height):
        t = y / max(1, height - 1)
        strip.putpixel((0, y), (
            int(bt[0] + (bb[0] - bt[0]) * t),
            int(bt[1] + (bb[1] - bt[1]) * t),
            int(bt[2] + (bb[2] - bt[2]) * t),
        ))
    img = strip.resize((width, height), Image.NEAREST)
    draw = ImageDraw.Draw(img)

    # Fonts. georgiabi = bold italic; fall back through bold then default.
    title_size = int(min(width, height) * (0.082 if is_portrait else 0.075))
    wordmark_size = int(title_size * 0.36)
    title_font = _load_font([
        "C:/Windows/Fonts/georgiabi.ttf",
        "C:/Windows/Fonts/georgiab.ttf",
        "C:/Windows/Fonts/georgia.ttf",
    ], title_size)
    wordmark_font = _load_font([
        "C:/Windows/Fonts/georgiab.ttf",
        "C:/Windows/Fonts/georgia.ttf",
    ], wordmark_size)

    accent = theme["accent"]
    wordmark = theme.get("wordmark")

    # Channel wordmark — letter-spaced uppercase, accent color, near top.
    if wordmark:
        spaced = "  ".join(list(wordmark))
        wm_bbox = draw.textbbox((0, 0), spaced, font=wordmark_font)
        wm_w = wm_bbox[2] - wm_bbox[0]
        wm_h = wm_bbox[3] - wm_bbox[1]
        wm_y = int(height * (0.22 if is_portrait else 0.18))
        wm_x = (width - wm_w) // 2
        draw.text((wm_x, wm_y), spaced, font=wordmark_font, fill=accent)

        # Thin accent rule under the wordmark.
        line_w = int(width * 0.32)
        line_y = wm_y + wm_h + int(wordmark_size * 0.9)
        line_x = (width - line_w) // 2
        draw.rectangle([line_x, line_y, line_x + line_w, line_y + 2], fill=accent)

    # Word-wrap by actually measuring each candidate line width with the
    # chosen font. Char-count heuristics overshoot for Georgia bold italic
    # (italic glyphs tilt and look narrower than monospace estimates).
    target_width = int(width * 0.88)
    words = title.split()
    lines: list[str] = []
    current = ""
    for w in words:
        candidate = (current + " " + w).strip()
        cb = draw.textbbox((0, 0), candidate, font=title_font)
        if cb[2] - cb[0] <= target_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    max_lines = 4 if is_portrait else 3
    if len(lines) > max_lines:
        kept = lines[:max_lines]
        kept[-1] = kept[-1].rstrip(" .,;:") + "..."
        lines = kept

    line_heights = [draw.textbbox((0, 0), ln, font=title_font)[3] -
                    draw.textbbox((0, 0), ln, font=title_font)[1]
                    for ln in lines]
    spacing = int(title_size * 0.25)
    total_h = sum(line_heights) + spacing * max(0, len(lines) - 1)

    # True vertical center — wordmark sits in the upper third on its own,
    # title block centers in the canvas. Negative space below reads as
    # deliberate poster composition rather than missing content.
    title_y = (height - total_h) // 2
    title_top = title_y
    title_bottom = title_y + total_h

    outline_w = max(3, title_size // 18)
    for line, lh in zip(lines, line_heights):
        bbox = draw.textbbox((0, 0), line, font=title_font)
        text_w = bbox[2] - bbox[0]
        x = (width - text_w) // 2
        # Black halo
        for dx in range(-outline_w, outline_w + 1):
            for dy in range(-outline_w, outline_w + 1):
                if dx * dx + dy * dy <= outline_w * outline_w:
                    draw.text((x + dx, title_y + dy), line, font=title_font,
                              fill=(0, 0, 0))
        # White fill
        draw.text((x, title_y), line, font=title_font, fill=(255, 255, 255))
        title_y += lh + spacing

    # Mirror accent rule below the title block — balances the wordmark
    # accent above and gives the bottom half a visual anchor.
    if wordmark:
        line_w = int(width * 0.32)
        rule_y = min(title_bottom + int(title_size * 0.9),
                     height - int(height * 0.10))
        rule_x = (width - line_w) // 2
        draw.rectangle([rule_x, rule_y, rule_x + line_w, rule_y + 2], fill=accent)

    img.save(output_path, "JPEG", quality=92)
    return output_path


def generate_thumbnail_from_video(video_path, title, output_path, width=1920, height=1080):
    """Extract first frame from video and generate thumbnail."""
    import subprocess
    import tempfile

    # Extract frame at 3 seconds
    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    tmp.close()
    subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-ss", "3", "-vframes", "1",
        "-vf", f"scale={width}:{height}",
        tmp.name,
    ], capture_output=True)

    if os.path.exists(tmp.name) and os.path.getsize(tmp.name) > 0:
        result = generate_thumbnail(tmp.name, title, output_path, width, height)
        os.unlink(tmp.name)
        return result

    os.unlink(tmp.name)
    return None


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", help="Source image path")
    parser.add_argument("--video", help="Source video path (extract frame)")
    parser.add_argument("--text-only", action="store_true",
                        help="Skip image source; render text-only thumbnail")
    parser.add_argument("--channel", help="Channel slug for text-only theming")
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()

    if args.text_only:
        generate_text_only_thumbnail(args.title, args.output, args.width,
                                     args.height, args.channel)
    elif args.image:
        generate_thumbnail(args.image, args.title, args.output, args.width, args.height)
    elif args.video:
        generate_thumbnail_from_video(args.video, args.title, args.output, args.width, args.height)
    print(f"Thumbnail: {args.output}")
