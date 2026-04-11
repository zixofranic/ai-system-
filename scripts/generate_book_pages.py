"""
Children's Book Page Generator — Who Stole My Birthday?
Uses whimsical_storybook LoRA + IP-Adapter for character consistency.
"""

import sys
import json
import time
import copy
import random
import requests
from pathlib import Path

COMFYUI_URL = "http://localhost:8188"
OUTPUT_DIR = Path("C:/AI/system/lora_datasets/book_pages")
CHAR_DIR = Path("C:/AI/system/lora_datasets/character_sheets")
MAYA_REF = Path("C:/Users/ziadf/Downloads/characterDesign/Untitled293_20230620134433.png")

# Workflow with LoRA + IP-Adapter
WORKFLOW_IPA = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "2": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "whimsical_storybook_v1.safetensors",
            "strength_model": 0.8,
            "strength_clip": 0.8,
            "model": ["1", 0],
            "clip": ["1", 1],
        },
    },
    "3": {
        "class_type": "IPAdapterUnifiedLoader",
        "inputs": {
            "model": ["2", 0],
            "preset": "PLUS (high strength)",
        },
    },
    "4": {
        "class_type": "LoadImage",
        "inputs": {"image": ""},
    },
    "5": {
        "class_type": "IPAdapterAdvanced",
        "inputs": {
            "model": ["3", 0],
            "ipadapter": ["3", 1],
            "image": ["4", 0],
            "weight": 0.45,
            "weight_type": "style transfer",
            "start_at": 0.0,
            "end_at": 0.3,
            "combine_embeds": "concat",
            "embeds_scaling": "K+V w/ C penalty",
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["2", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "photorealistic, 3d render, anime, manga, text, watermark, logo, deformed, ugly, blurry, low quality, dark, scary, horror, flat vector, clip art, animal, furry, anthropomorphic animal, adult, old man, beard, mature, elderly",
            "clip": ["2", 1],
        },
    },
    "8": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 35,
            "cfg": 7.0,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["5", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["9", 0],
        },
    },
    "9": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1216, "height": 832, "batch_size": 1},
    },
    "10": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["8", 0], "vae": ["1", 2]},
    },
    "11": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "page", "images": ["10", 0]},
    },
}

# No IP-Adapter version
WORKFLOW_NO_IPA = {
    "1": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
    },
    "2": {
        "class_type": "LoraLoader",
        "inputs": {
            "lora_name": "whimsical_storybook_v1.safetensors",
            "strength_model": 0.8,
            "strength_clip": 0.8,
            "model": ["1", 0],
            "clip": ["1", 1],
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {"text": "", "clip": ["2", 1]},
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "photorealistic, 3d render, anime, manga, text, watermark, logo, deformed, ugly, blurry, low quality, dark, scary, horror, flat vector, clip art, animal, furry, anthropomorphic animal, adult, old man, beard, mature, elderly",
            "clip": ["2", 1],
        },
    },
    "8": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 35,
            "cfg": 7.5,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["2", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["9", 0],
        },
    },
    "9": {
        "class_type": "EmptyLatentImage",
        "inputs": {"width": 1216, "height": 832, "batch_size": 1},
    },
    "10": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["8", 0], "vae": ["1", 2]},
    },
    "11": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "page", "images": ["10", 0]},
    },
}

STYLE_PREFIX = "whimsical_storybook, children's book illustration, soft painterly textures, warm colors, storybook art, "
MAYA_DESC = "young girl Maya with pink magenta hair, big expressive eyes, rosy cheeks, white t-shirt with rocket graphic, black pants, orange boots, "

# reference image to use per page: "maya" = Maya ref, "circle" etc = pirate sheet, None = no IPA
PAGES = [
    {"num": 1, "name": "title_cover", "ref": "maya", "wide": False,
     "prompt": f"{MAYA_DESC}standing on a sandy beach holding yellow teddy bear, five colorful islands in the background shaped like geometric shapes, pirate ship silhouette in morning fog, bright cheerful title page composition"},
    {"num": 2, "name": "maya_wakes_up", "ref": "maya",
     "prompt": f"{MAYA_DESC}sitting up in bed surprised, messy hair, cozy bedroom, morning light through window, teddy bear on pillow, birthday decorations visible in room, startled expression"},
    {"num": 3, "name": "ship_in_fog", "ref": "maya",
     "prompt": f"{MAYA_DESC}looking out of a window from behind, misty morning seascape, gray pirate ship silhouette sailing away in fog, calm ocean, golden sunrise light, mysterious atmosphere"},
    {"num": 4, "name": "the_bottle", "ref": "maya",
     "prompt": f"{MAYA_DESC}leaning out of a window reaching down, glass bottle with rolled paper inside sitting on sandy ground below window, morning light, curious expression"},
    {"num": 5, "name": "pirate_message", "ref": None,
     "prompt": "close-up of an old pirate letter with messy handwriting on parchment paper, skull and crossbones doodle, torn edges, held by small child hands, dramatic lighting"},
    {"num": 6, "name": "maya_angry", "ref": "maya",
     "prompt": f"{MAYA_DESC}standing with fists clenched holding teddy bear tight, determined angry face, bedroom background, morning light, brave pose"},
    {"num": 7, "name": "little_boat", "ref": "maya",
     "prompt": f"{MAYA_DESC}in a small colorful wooden sailboat on rough ocean waves, teddy bear beside her, adventurous expression, big waves, cloudy sky, wide ocean view"},
    {"num": 8, "name": "shape_islands", "ref": None,
     "prompt": "aerial view of five colorful tropical islands in the ocean, each island a different geometric shape circle diamond triangle rectangle hexagon, lush vegetation, sparkling blue water, magical golden glow, treasure map style"},
    {"num": 9, "name": "circle_island_arrival", "ref": "circle",
     "prompt": f"round circle-shaped island, chubby round boy pirate child in orange and brown pirate outfit waving on the beach, round trees round rocks, {MAYA_DESC}small boat approaching, colorful tropical setting"},
    {"num": 10, "name": "circle_balloons", "ref": "circle",
     "prompt": f"{MAYA_DESC}talking to chubby round boy pirate child on circular island, pirate surrounded by hundreds of colorful round balloons, pirate looking surprised and innocent, tropical beach"},
    {"num": 11, "name": "circle_gift", "ref": "circle",
     "prompt": f"chubby round boy pirate child handing colorful balloons to smiling {MAYA_DESC}warm friendly interaction, tropical circular island background, bright colors, kind gesture"},
    {"num": 12, "name": "flying_to_diamond", "ref": "maya",
     "prompt": f"{MAYA_DESC}floating through the sky holding colorful balloons and teddy bear, seen from below against blue sky with white clouds, diamond-shaped island visible below, joyful expression"},
    {"num": 13, "name": "diamond_island", "ref": "diamond",
     "prompt": f"diamond-shaped island, slim tall boy pirate child in blue and silver pirate outfit hanging diamond-shaped piñata on tree, many diamond piñatas in trees, {MAYA_DESC}landing with balloons, sparkling decorations"},
    {"num": 14, "name": "diamond_gift", "ref": "diamond",
     "prompt": f"slim tall boy pirate child in blue silver outfit handing bright yellow diamond piñata to {MAYA_DESC}already holding balloons, warm interaction on diamond island, tropical trees"},
    {"num": 15, "name": "flying_to_triangle", "ref": "maya",
     "prompt": f"{MAYA_DESC}floating through sky with balloons and yellow piñata and teddy bear, triangle-shaped island visible ahead, fluffy clouds, ocean below, hopeful expression"},
    {"num": 16, "name": "triangle_island", "ref": "triangle",
     "prompt": f"triangle-shaped island, spiky energetic boy pirate child in green yellow outfit greeting {MAYA_DESC}triangular trees and rocks, colorful triangle decorations everywhere, Maya holding balloons and piñata"},
    {"num": 17, "name": "triangle_gift", "ref": "triangle",
     "prompt": f"{MAYA_DESC}happily picking up colorful triangle decorations from the ground, spiky boy pirate child watching proudly, piles of triangles in many colors, festive atmosphere, triangle island"},
    {"num": 18, "name": "flying_to_rectangle", "ref": "maya",
     "prompt": f"{MAYA_DESC}flying through fluffy white clouds carrying balloons piñata triangles and teddy bear, rectangle-shaped island visible below through cloud gap, golden sunlight, magical adventure"},
    {"num": 19, "name": "rectangle_arrival", "ref": "rectangle",
     "prompt": f"rectangle-shaped island, tall blocky boy pirate child in red purple outfit jumping up and down waving excitedly, rectangular trees and rocks, {MAYA_DESC}approaching with collected items, energetic scene"},
    {"num": 20, "name": "rectangle_cards", "ref": "rectangle",
     "prompt": f"tall blocky boy pirate child in red purple outfit showing colorful rectangular invitation cards to {MAYA_DESC}rectangle island background, pirate looking confident, Maya with wide surprised eyes"},
    {"num": 21, "name": "secret_map", "ref": "rectangle",
     "prompt": f"tall blocky boy pirate child blowing on an invitation card revealing a glowing magical secret map, {MAYA_DESC}watching in amazement, sparkles and magic dust in the air, mysterious magical moment"},
    {"num": 22, "name": "heading_to_hexagon", "ref": "maya",
     "prompt": f"{MAYA_DESC}floating away carrying all gifts balloons piñata triangles cards and teddy bear, determined brave expression, hexagon-shaped island visible in the distance, sunset sky"},
    {"num": 23, "name": "hexagon_in_sight", "ref": "maya",
     "prompt": f"large hexagon-shaped island covered in birthday decorations balloons streamers cake, viewed from distance, {MAYA_DESC}approaching with shocked surprised expression, elaborately decorated party island"},
    {"num": 24, "name": "maya_lands", "ref": "maya",
     "prompt": f"{MAYA_DESC}standing alone on hexagon island surrounded by birthday decorations, hexagonal birthday cake on a table, streamers and balloons everywhere, Maya yelling with hands cupped around mouth, empty island"},
    {"num": 25, "name": "countdown", "ref": "maya",
     "prompt": f"{MAYA_DESC}standing alone looking confused and curious, subtle shadows of hiding figures behind a large rock, mysterious atmosphere, birthday-decorated hexagon island, suspenseful moment"},
    {"num": 26, "name": "surprise_spread", "ref": "maya", "wide": True,
     "prompt": f"five boy pirate children in geometric shape outfits circle diamond triangle rectangle hexagon jumping out from behind a rock surprising {MAYA_DESC}confetti streamers balloons flying everywhere, huge celebration, Maya shocked and delighted, birthday party, dynamic joyful scene, wide panoramic"},
    {"num": 27, "name": "best_birthday", "ref": "hexagon",
     "prompt": f"stocky boy pirate child in purple gold outfit talking to overjoyed {MAYA_DESC}all five boy pirate children around her smiling, birthday party fully set up, cake table decorations balloons, Maya jumping with joy holding teddy bear, warm celebration"},
    {"num": 28, "name": "the_party", "ref": None,
     "prompt": f"big birthday party scene on hexagon island, five boy pirate children in colorful geometric outfits dancing with {MAYA_DESC}in the center, birthday cake balloons piñata decorations music notes in air, confetti falling, joyful celebration, warm golden light"},
    {"num": 29, "name": "back_cover", "ref": None,
     "prompt": "five boy pirate children in geometric shape outfits waving goodbye from a colorful island, small sailboat on calm ocean at sunset, warm golden colors, peaceful ending, back cover illustration"},
]

REF_MAP = {
    "maya": str(MAYA_REF),
    "circle": str(CHAR_DIR / "circle_pirate.png"),
    "diamond": str(CHAR_DIR / "diamond_pirate.png"),
    "triangle": str(CHAR_DIR / "triangle_pirate.png"),
    "rectangle": str(CHAR_DIR / "rectangle_pirate.png"),
    "hexagon": str(CHAR_DIR / "hexagon_pirate.png"),
}


def upload_image(filepath: str) -> str:
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (Path(filepath).name, f, "image/png")},
            data={"overwrite": "true"},
        )
    resp.raise_for_status()
    return resp.json()["name"]


def generate(workflow, prompt, output_path, ref_image=None, width=1216, height=832):
    wf = copy.deepcopy(workflow)
    wf["8"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)
    wf["6"]["inputs"]["text"] = STYLE_PREFIX + prompt
    wf["9"]["inputs"]["width"] = width
    wf["9"]["inputs"]["height"] = height
    wf["11"]["inputs"]["filename_prefix"] = Path(output_path).stem

    if ref_image and "4" in wf:
        wf["4"]["inputs"]["image"] = ref_image

    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": wf}, timeout=30)
    resp.raise_for_status()
    prompt_id = resp.json()["prompt_id"]

    for _ in range(120):
        time.sleep(3)
        hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=15).json()
        if prompt_id in hist:
            outputs = hist[prompt_id].get("outputs", {})
            for node_output in outputs.values():
                images = node_output.get("images", [])
                if images:
                    params = {
                        "filename": images[0]["filename"],
                        "subfolder": images[0].get("subfolder", ""),
                        "type": "output",
                    }
                    img = requests.get(f"{COMFYUI_URL}/view", params=params, timeout=30)
                    img.raise_for_status()
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img.content)
                    return output_path
    raise TimeoutError(f"Timeout: {output_path}")


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Pre-upload all reference images
    print("Uploading reference images...")
    uploaded_refs = {}
    for key, path in REF_MAP.items():
        if Path(path).exists():
            uploaded_refs[key] = upload_image(path)
            print(f"  {key}: {uploaded_refs[key]}")

    total = len(PAGES)
    for i, page in enumerate(PAGES):
        num = page["num"]
        name = page["name"]
        ref_key = page.get("ref")
        is_wide = page.get("wide", False)

        # Title page is portrait, spread is extra wide, rest are landscape
        if num == 1 and not is_wide:
            w, h = 832, 1216  # portrait for cover
        elif is_wide:
            w, h = 1536, 832  # extra wide for double spread
        else:
            w, h = 1216, 832  # landscape

        use_ipa = ref_key is not None and ref_key in uploaded_refs
        wf = WORKFLOW_IPA if use_ipa else WORKFLOW_NO_IPA
        ref_img = uploaded_refs.get(ref_key) if use_ipa else None

        output_path = str(OUTPUT_DIR / f"page_{num:02d}_{name}.png")

        print(f"\n[{i+1}/{total}] Page {num}: {name}")
        print(f"  Ref: {ref_key or 'none'}  Size: {w}x{h}")

        try:
            generate(wf, page["prompt"], output_path, ref_image=ref_img, width=w, height=h)
            print(f"  OK")
        except Exception as e:
            print(f"  FAILED: {e}")

    print(f"\nDone! {total} pages saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
