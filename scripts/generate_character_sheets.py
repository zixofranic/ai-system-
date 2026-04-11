"""
Character Sheet Generator for Children's Book
Uses IP-Adapter + whimsical_storybook LoRA to create consistent character designs.
"""

import sys
import json
import time
import copy
import random
import requests
from pathlib import Path

COMFYUI_URL = "http://localhost:8188"
OUTPUT_DIR = Path("C:/AI/system/lora_datasets/character_sheets")
REFERENCE_DIR = Path("C:/Users/ziadf/Downloads/characterDesign")

# Workflow with LoRA + IP-Adapter
WORKFLOW = {
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
        "inputs": {"image": ""},  # filled per generation
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
            "text": "photorealistic, 3d render, anime, manga, text, watermark, logo, deformed, ugly, blurry, low quality, dark, scary, horror, flat vector, clip art, animal, furry, anthropomorphic animal, cat, dog, bear character, adult, old man, beard, mature, elderly, wrinkles",
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
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "10": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["8", 0], "vae": ["1", 2]},
    },
    "11": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "charsheet", "images": ["10", 0]},
    },
}

# No IP-Adapter version (for pirates that don't have reference images)
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
            "text": "photorealistic, 3d render, anime, manga, text, watermark, logo, deformed, ugly, blurry, low quality, dark, scary, horror, flat vector, clip art, animal, furry, anthropomorphic animal, cat, dog, bear character, adult, old man, beard, mature, elderly, wrinkles",
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
        "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
    },
    "10": {
        "class_type": "VAEDecode",
        "inputs": {"samples": ["8", 0], "vae": ["1", 2]},
    },
    "11": {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": "charsheet", "images": ["10", 0]},
    },
}


def upload_image(filepath: str) -> str:
    """Upload a reference image to ComfyUI and return its internal name."""
    with open(filepath, "rb") as f:
        resp = requests.post(
            f"{COMFYUI_URL}/upload/image",
            files={"image": (Path(filepath).name, f, "image/png")},
            data={"overwrite": "true"},
        )
    resp.raise_for_status()
    return resp.json()["name"]


def generate(workflow: dict, prompt: str, output_path: str,
             reference_image: str = None, width: int = 1024, height: int = 1024):
    """Generate a single image."""
    wf = copy.deepcopy(workflow)
    wf["8"]["inputs"]["seed"] = random.randint(0, 2**32 - 1)
    wf["6"]["inputs"]["text"] = prompt
    wf["9"]["inputs"]["width"] = width
    wf["9"]["inputs"]["height"] = height
    wf["11"]["inputs"]["filename_prefix"] = Path(output_path).stem

    if reference_image and "4" in wf:
        wf["4"]["inputs"]["image"] = reference_image

    resp = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": wf}, timeout=30)
    resp.raise_for_status()
    prompt_id = resp.json()["prompt_id"]

    for _ in range(100):
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


# ---------------------------------------------------------------------------
# Character definitions
# ---------------------------------------------------------------------------
CHARACTERS = {
    "maya": {
        "use_ipadapter": True,
        "reference": str(REFERENCE_DIR / "Untitled293_20230620134433.png"),
        "sheets": [
            {
                "name": "maya_front_poses",
                "prompt": (
                    "whimsical_storybook, character design sheet, young girl Maya, "
                    "pink magenta hair side-swept, big expressive round eyes, rosy cheeks, "
                    "white t-shirt with rocket graphic, black pants, orange boots, "
                    "holding a yellow teddy bear, multiple poses on white background, "
                    "front view standing, waving, hands on hips, jumping with joy, "
                    "character turnaround reference sheet, children's book illustration"
                ),
            },
            {
                "name": "maya_expressions",
                "prompt": (
                    "whimsical_storybook, character expression sheet, young girl Maya, "
                    "pink magenta hair, big expressive eyes, rosy cheeks, "
                    "six facial expressions on white background: happy smiling, surprised shocked, "
                    "angry determined, sad crying, brave confident, laughing, "
                    "head and shoulders close-up views, children's book illustration"
                ),
            },
            {
                "name": "maya_adventure",
                "prompt": (
                    "whimsical_storybook, character action sheet, young girl Maya, "
                    "pink magenta hair, white rocket t-shirt, black pants, orange boots, "
                    "yellow teddy bear, multiple action poses on white background: "
                    "sailing a small boat, floating with balloons, running, pointing forward, "
                    "looking through a telescope, children's book illustration"
                ),
            },
        ],
    },
    "circle_pirate": {
        "use_ipadapter": False,
        "sheets": [
            {
                "name": "circle_pirate",
                "prompt": (
                    "whimsical_storybook, character design sheet, Circle Pirate, "
                    "a chubby round human boy pirate child age 8 with a circular body shape, "
                    "round pirate hat, round chubby cheeks, stubby arms and legs, "
                    "big round nose, cheerful smile, pirate eye patch, young boy face, "
                    "warm orange and brown pirate outfit, "
                    "multiple poses on white background: waving, holding balloons, standing proud, "
                    "character reference sheet, children's book illustration, human child only"
                ),
            },
        ],
    },
    "diamond_pirate": {
        "use_ipadapter": False,
        "sheets": [
            {
                "name": "diamond_pirate",
                "prompt": (
                    "whimsical_storybook, character design sheet, Diamond Pirate, "
                    "a slim tall human boy pirate child age 8 with a diamond-shaped body silhouette, "
                    "diamond-shaped pirate hat, angular young face, "
                    "sparkly blue and silver pirate outfit, crystal decorations, "
                    "thin angular limbs, smug confident expression, young boy face, "
                    "multiple poses on white background: hanging piñata, bowing, standing tall, "
                    "character reference sheet, children's book illustration, human child only"
                ),
            },
        ],
    },
    "triangle_pirate": {
        "use_ipadapter": False,
        "sheets": [
            {
                "name": "triangle_pirate",
                "prompt": (
                    "whimsical_storybook, character design sheet, Triangle Pirate, "
                    "a spiky energetic human boy pirate child age 8 with a triangle-shaped body wide at bottom pointy at top, "
                    "triangular pirate hat, pointy spiky hair, sharp triangular features, "
                    "green and yellow pirate outfit, triangular decorations, young boy face, "
                    "friendly energetic expression, pointy boots, "
                    "multiple poses on white background: giving gifts, pointing, laughing, "
                    "character reference sheet, children's book illustration, human child only"
                ),
            },
        ],
    },
    "rectangle_pirate": {
        "use_ipadapter": False,
        "sheets": [
            {
                "name": "rectangle_pirate",
                "prompt": (
                    "whimsical_storybook, character design sheet, Rectangle Pirate, "
                    "a tall blocky human boy pirate child age 8 with a rectangular body shape, "
                    "rectangular pirate hat flat top, square jaw, broad shoulders for a kid, "
                    "red and purple pirate outfit, rectangular buckle belt, young boy face, "
                    "excited hyperactive expression, "
                    "multiple poses on white background: jumping excitedly, showing cards, flexing, "
                    "character reference sheet, children's book illustration, human child only"
                ),
            },
        ],
    },
    "hexagon_pirate": {
        "use_ipadapter": False,
        "sheets": [
            {
                "name": "hexagon_pirate",
                "prompt": (
                    "whimsical_storybook, character design sheet, Hexagon Pirate, "
                    "a bigger stocky human boy pirate child age 9 with a hexagonal body shape, the oldest brother, "
                    "large hexagonal pirate captain hat with feather, bossy but kind, young boy face, "
                    "deep purple and gold pirate outfit, hexagonal jewels and buttons, "
                    "mischievous but kind expression, stocky sturdy kid build, "
                    "multiple poses on white background: arms crossed boss pose, laughing, surprising someone, "
                    "character reference sheet, children's book illustration, human child only"
                ),
            },
        ],
    },
    "environment_beach": {
        "use_ipadapter": False,
        "sheets": [
            {
                "name": "env_beach_boat",
                "prompt": (
                    "whimsical_storybook, environment concept art, "
                    "sandy tropical beach with a small colorful wooden sailboat, "
                    "calm turquoise ocean, soft morning light, palm trees, "
                    "cozy beach cottage in the background, seashells on sand, "
                    "warm inviting colors, children's book illustration"
                ),
            },
        ],
    },
    "environment_islands": {
        "use_ipadapter": False,
        "sheets": [
            {
                "name": "env_shape_islands",
                "prompt": (
                    "whimsical_storybook, environment concept art, aerial map view, "
                    "five colorful tropical islands in the ocean, each island a distinct geometric shape: "
                    "circle island, diamond island, triangle island, rectangle island, hexagon island, "
                    "sparkling blue water between islands, lush green vegetation, "
                    "treasure map style with golden glow, children's book illustration"
                ),
            },
        ],
    },
}


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Upload Maya reference image
    print("Uploading Maya reference image...")
    maya_ref = upload_image(str(REFERENCE_DIR / "Untitled293_20230620134433.png"))
    print(f"  Uploaded as: {maya_ref}")

    total = sum(len(c["sheets"]) for c in CHARACTERS.values())
    done = 0

    for char_name, char_config in CHARACTERS.items():
        use_ipa = char_config.get("use_ipadapter", False)
        wf = WORKFLOW if use_ipa else WORKFLOW_NO_IPA

        for sheet in char_config["sheets"]:
            done += 1
            name = sheet["name"]
            prompt = sheet["prompt"]
            output_path = str(OUTPUT_DIR / f"{name}.png")

            print(f"\n[{done}/{total}] Generating: {name}")
            print(f"  IP-Adapter: {'Yes' if use_ipa else 'No'}")

            try:
                ref = maya_ref if use_ipa else None
                generate(wf, prompt, output_path, reference_image=ref)
                print(f"  OK: {name}.png")
            except Exception as e:
                print(f"  FAILED: {e}")

    print(f"\nDone! Character sheets saved to {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
