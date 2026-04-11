import sys, os
os.environ["IMAGEMAGICK_BINARY"] = "C:/Program Files/ImageMagick-7.1.2-Q16-HDRI/magick.exe"
from moviepy.editor import TextClip, ColorClip, CompositeVideoClip, ImageClip

W, H = 1080, 1920
art_path = "C:/AI/system/pipeline_work/9626d4dd-45fb-43b0-beaf-706411d6101d/art_0.png"
quote = "There is a particular darkness that does not announce itself at the door — it simply moves in, rearranges the furniture of your mind, and convinces you it was always this way."
philosopher = "Seneca"

art = ImageClip(art_path).resize(height=H)
if art.w < W:
    art = art.resize(width=W)
canvas = ColorClip(size=(W, H), color=(0, 0, 0)).set_duration(1)
art_layer = CompositeVideoClip([canvas, art.set_position("center")], size=(W, H)).set_duration(1)

text_w = int(W * 0.82)
quote_y_rel = 0.42

quote_clip = TextClip(
    quote, fontsize=46, color="white", font="Calibri-Bold",
    method="caption", size=(text_w, None), stroke_color="black",
    stroke_width=2, align="center",
).set_position(("center", quote_y_rel), relative=True).set_duration(1)

overlay_top = int(H * quote_y_rel) - 60
overlay_h = int(H * 0.82) - overlay_top
text_overlay = ColorClip(size=(W, overlay_h), color=(0, 0, 0)).set_opacity(0.65).set_position((0, overlay_top)).set_duration(1)

attr_clip = TextClip(
    f"— {philosopher}", fontsize=36, color="#D4AF37",
    font="Calibri", method="label", stroke_color="black", stroke_width=1,
).set_position(("center", 0.75), relative=True).set_duration(1)

frame = CompositeVideoClip([art_layer, text_overlay, quote_clip, attr_clip], size=(W, H)).set_duration(1)
frame.save_frame("C:/AI/system/scripts/test_frame_calibri.png", t=0)

# Also try Book Antiqua (elegant serif, cleaner than Garamond)
quote_clip2 = TextClip(
    quote, fontsize=46, color="white", font="Book-Antiqua-Bold",
    method="caption", size=(text_w, None), stroke_color="black",
    stroke_width=2, align="center",
).set_position(("center", quote_y_rel), relative=True).set_duration(1)

attr_clip2 = TextClip(
    f"— {philosopher}", fontsize=36, color="#D4AF37",
    font="Book-Antiqua", method="label", stroke_color="black", stroke_width=1,
).set_position(("center", 0.75), relative=True).set_duration(1)

frame2 = CompositeVideoClip([art_layer, text_overlay, quote_clip2, attr_clip2], size=(W, H)).set_duration(1)
frame2.save_frame("C:/AI/system/scripts/test_frame_bookantiqua.png", t=0)
print("Saved both")
