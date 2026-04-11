import sys, os
os.environ["IMAGEMAGICK_BINARY"] = "C:/Program Files/ImageMagick-7.1.2-Q16-HDRI/magick.exe"
sys.path.insert(0, "C:/AI/system/scripts")

from assemble_video import assemble_video
from pathlib import Path
import glob

work = "C:/AI/system/pipeline_work/9626d4dd-45fb-43b0-beaf-706411d6101d"
output = f"{work}/wisdom_9626d4dd_v2.mp4"

# Find a music file
music_dir = Path("C:/AI/system/music/stoic_classical")
music_files = list(music_dir.glob("*.mp3"))
if not music_files:
    music_files = list(Path("C:/AI/system/music").rglob("*.mp3"))
music_path = str(music_files[0])

print(f"Art: {work}/art_0.png")
print(f"Voice: {work}/voice_0.wav")
print(f"Music: {music_path}")
print(f"Output: {output}")

assemble_video(
    quotes=["The Tao that can be told is not the eternal Tao. Stop forcing change — let it arrive on its own terms, in its own time."],
    philosopher="Lao Tzu",
    art_paths=[f"{work}/art_0.png"],
    voice_paths=[f"{work}/voice_0.wav"],
    music_path=music_path,
    output_path=output,
    format="short",
    aspect_ratio="9:16",
    channel_name="Wisdom",
    equalizer_color="#D4AF37",
)
print(f"\nDONE: {output}")
