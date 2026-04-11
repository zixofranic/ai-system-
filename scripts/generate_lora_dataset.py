"""
LoRA Training Dataset Generator
================================
Generates a consistent set of images via ComfyUI (SDXL base, no LoRA)
for training a new LoRA style.

Usage:
    python generate_lora_dataset.py --style whimsical_storybook --count 30
"""

import sys
import json
import time
import random
import argparse
import requests
import copy
from pathlib import Path

COMFYUI_URL = "http://localhost:8188"

# ---------------------------------------------------------------------------
# Workflow template (SDXL base, NO LoRA — we want pure SDXL + prompt style)
# ---------------------------------------------------------------------------
WORKFLOW_TEMPLATE = {
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": 0,
            "steps": 35,
            "cfg": 7.5,
            "sampler_name": "euler_ancestral",
            "scheduler": "normal",
            "denoise": 1.0,
            "model": ["10", 0],
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "5": {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": 1024,
            "height": 1024,
            "batch_size": 1,
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "",
            "clip": ["10", 1],
        },
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "",
            "clip": ["10", 1],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["3", 0],
            "vae": ["10", 2],
        },
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "lora_train",
            "images": ["8", 0],
        },
    },
    "10": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": "sd_xl_base_1.0.safetensors",
        },
    },
}

# ---------------------------------------------------------------------------
# Style definitions — each style has a prefix, negative, and scene prompts
# ---------------------------------------------------------------------------
STYLES = {
    "whimsical_storybook": {
        "trigger": "whimsical_storybook",
        "description": "Whimsical digital children's book illustration with painterly textures",
        "style_prefix": (
            "whimsical children's book illustration, digital painting with visible brush strokes, "
            "soft painterly textures, warm muted color palette with pops of color, "
            "charming storybook art style, slightly stylized characters with expressive faces, "
            "gentle lighting, textured paper feel, hand-painted digital art, "
            "cozy atmospheric illustration, professional picture book quality"
        ),
        "negative": (
            "photorealistic, 3d render, anime, manga, pixel art, low quality, blurry, "
            "text, watermark, logo, deformed, ugly, grotesque, scary, horror, "
            "oversaturated neon colors, flat vector art, clip art, stock photo, "
            "adult content, violent, dark gritty"
        ),
        "scenes": [
            # Characters in various settings (diversity of subjects)
            "a young girl with braided hair reading a large book under a giant oak tree, autumn leaves falling, a small fox curled up beside her",
            "a boy in a striped sweater and his grandfather walking through a misty forest, collecting mushrooms in a wicker basket",
            "two children building a blanket fort in a cozy living room, fairy lights glowing, a cat peeking from behind pillows",
            "a little girl in rain boots jumping in puddles on a cobblestone street, her red umbrella catching the wind",
            "a boy sitting on a dock, feet dangling over calm water, fishing rod in hand, a frog on a lily pad nearby",
            "a child riding on the back of a gentle giant bear through a snowy pine forest at dusk",
            "three friends having a tea party in a garden, surrounded by oversized flowers and curious butterflies",
            "a girl with curly hair painting at an easel in a sunlit attic room, paint splatters everywhere",
            "a boy and his dog exploring a tide pool at the beach, starfish and crabs visible in the clear water",
            "a child astronaut floating among stars and planets, holding a stuffed rabbit, wearing a fishbowl helmet",

            # Animals as characters
            "a fox wearing a scarf walking through an autumn village, carrying a basket of apples",
            "a family of rabbits having dinner around a tiny wooden table inside their burrow home",
            "an owl librarian with round spectacles organizing books on towering shelves in a tree hollow",
            "a mouse sailing a walnut shell boat across a pond, using a leaf as a sail",
            "a bear cub and a deer fawn sharing berries on a mossy log in a sun-dappled clearing",

            # Adventure scenes
            "children sailing a small wooden boat through enormous ocean waves, a whale breaching in the distance",
            "a girl discovering a hidden door in an old garden wall, vines and flowers framing the entrance, golden light spilling through",
            "a boy climbing a beanstalk that disappears into fluffy clouds, a village tiny below",
            "two children riding bicycles down a country lane at sunset, fireflies beginning to glow",
            "a child opening a treasure chest in a cave, the golden glow illuminating their amazed face",

            # Cozy / emotional scenes
            "a mother reading a bedtime story to two children tucked in bed, warm lamplight, shadows on the wall",
            "a child hugging a large stuffed elephant while looking out a rain-streaked window",
            "an elderly woman and a small child baking cookies together in a warm kitchen, flour dust in the air",
            "a boy planting a small seedling in a garden, a watering can beside him, morning dew on the grass",
            "two siblings lying in tall grass watching clouds shaped like animals drift by",

            # Fantasy / magical
            "a fairy village built into the roots of an ancient tree, tiny lanterns glowing, mushroom houses",
            "a child following a trail of glowing fireflies through a magical twilight forest",
            "a dragon the size of a house cat curled up on a child's lap, both sleeping by a fireplace",
            "a wizard's tower library with floating books and spiral staircases, a child reaching for a glowing tome",
            "a mermaid child playing with dolphins in a coral reef, sunlight filtering through turquoise water",
        ],
    },
}


def generate_image(prompt: str, negative: str, output_path: str,
                   width: int = 1024, height: int = 1024):
    """Generate a single image via ComfyUI API."""
    workflow = copy.deepcopy(WORKFLOW_TEMPLATE)
    workflow["3"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)
    workflow["5"]["inputs"]["width"] = width
    workflow["5"]["inputs"]["height"] = height
    workflow["6"]["inputs"]["text"] = prompt
    workflow["7"]["inputs"]["text"] = negative
    workflow["9"]["inputs"]["filename_prefix"] = Path(output_path).stem

    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow}, timeout=30)
    resp.raise_for_status()
    prompt_id = resp.json()["prompt_id"]

    # Poll for completion
    for _ in range(100):  # 5 min max
        time.sleep(3)
        hist = requests.get(f"{COMFYUI_URL}/history/{prompt_id}", timeout=15).json()
        if prompt_id in hist:
            outputs = hist[prompt_id].get("outputs", {})
            for node_output in outputs.values():
                images = node_output.get("images", [])
                if images:
                    img_info = images[0]
                    params = {
                        "filename": img_info["filename"],
                        "subfolder": img_info.get("subfolder", ""),
                        "type": "output",
                    }
                    img_resp = requests.get(f"{COMFYUI_URL}/view", params=params, timeout=30)
                    img_resp.raise_for_status()
                    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
                    with open(output_path, "wb") as f:
                        f.write(img_resp.content)
                    return output_path

    raise TimeoutError(f"ComfyUI timeout for {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Generate LoRA training dataset")
    parser.add_argument("--style", default="whimsical_storybook",
                        choices=list(STYLES.keys()))
    parser.add_argument("--count", type=int, default=30,
                        help="Number of images to generate")
    parser.add_argument("--output", default=None,
                        help="Output directory (default: C:/AI/system/lora_datasets/{style})")
    args = parser.parse_args()

    style = STYLES[args.style]
    output_dir = Path(args.output) if args.output else Path(f"C:/AI/system/lora_datasets/{args.style}")
    output_dir.mkdir(parents=True, exist_ok=True)

    scenes = style["scenes"]
    count = min(args.count, len(scenes))

    print(f"Generating {count} images for style: {args.style}")
    print(f"Output: {output_dir}")
    print(f"Trigger word: {style['trigger']}")
    print()

    # Save metadata for training
    metadata = {
        "style": args.style,
        "trigger_word": style["trigger"],
        "description": style["description"],
        "count": count,
        "prompts": [],
    }

    for i in range(count):
        scene = scenes[i % len(scenes)]
        full_prompt = f"{style['style_prefix']}, {scene}"
        negative = style["negative"]
        output_path = str(output_dir / f"{args.style}_{i:03d}.png")

        print(f"[{i+1}/{count}] {scene[:70]}...")

        try:
            generate_image(full_prompt, negative, output_path)
            # Write caption file for kohya training (trigger + scene description)
            caption_path = output_dir / f"{args.style}_{i:03d}.txt"
            caption_path.write_text(f"{style['trigger']}, {scene}")
            metadata["prompts"].append({"index": i, "scene": scene, "file": Path(output_path).name})
            print(f"  OK: {Path(output_path).name}")
        except Exception as e:
            print(f"  FAILED: {e}")

    # Save metadata
    with open(output_dir / "dataset_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nDone! {count} images saved to {output_dir}")
    print(f"Caption files (.txt) ready for kohya_ss training.")
    print(f"Trigger word: {style['trigger']}")


if __name__ == "__main__":
    main()
