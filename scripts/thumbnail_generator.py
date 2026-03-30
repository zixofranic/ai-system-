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
from PIL import Image, ImageDraw, ImageFont


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
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args()

    if args.image:
        generate_thumbnail(args.image, args.title, args.output, args.width, args.height)
    elif args.video:
        generate_thumbnail_from_video(args.video, args.title, args.output, args.width, args.height)
    print(f"Thumbnail: {args.output}")
