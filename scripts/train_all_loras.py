"""
Train ALL remaining LoRAs back-to-back. No stopping.
Checks what's already trained, skips those, trains the rest.
"""

import subprocess
import shutil
import time
from pathlib import Path

KOHYA_DIR = Path("C:/AI/system/kohya_ss/sd-scripts")
COMFYUI_LORAS = Path("C:/AI/system/ComfyUI/models/loras")
BASE_MODEL = "C:/AI/system/ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors"
PYTHON = "C:/Users/ziadf/miniconda3/envs/lora_train/python.exe"

LORA_CONFIGS = [
    {
        "name": "romantic_landscape_v1",
        "source": "C:/AI/wisdom/loras/romantic_landscape_training",
        "kohya_dir": "C:/AI/wisdom/loras/romantic_landscape_kohya",
        "output_dir": "C:/AI/wisdom/loras/romantic_landscape_output",
        "config": "C:/AI/wisdom/loras/romantic_landscape_config.toml",
        "concept_name": "18_romantic_landscape",
        "caption": (
            "romantic_landscape painting, Hudson River School style, "
            "golden hour sunlight through ancient trees, luminous clouds, "
            "majestic mountain vista with river valley, "
            "warm amber and green palette, sublime nature grandeur, "
            "American romantic landscape oil painting, dramatic sky"
        ),
    },
    {
        "name": "dark_expressionist_v1",
        "source": "C:/AI/wisdom/loras/dark_expressionist_training",
        "kohya_dir": "C:/AI/wisdom/loras/dark_expressionist_kohya",
        "output_dir": "C:/AI/wisdom/loras/dark_expressionist_output",
        "concept_name": "18_dark_expressionist",
        "caption": (
            "dark_expressionist painting, intense dramatic composition, "
            "heavy chiaroscuro shadows, tormented emotional atmosphere, "
            "German expressionist and dark romantic style, "
            "storm clouds and angular forms, existential anguish, "
            "Munch and Friedrich inspired, psychological depth"
        ),
    },
    {
        "name": "aesthetic_gilded_v1",
        "source": "C:/AI/wisdom/loras/aesthetic_gilded_training",
        "kohya_dir": "C:/AI/wisdom/loras/aesthetic_gilded_kohya",
        "output_dir": "C:/AI/wisdom/loras/aesthetic_gilded_output",
        "concept_name": "15_aesthetic_gilded",
        "caption": (
            "aesthetic_gilded painting, art nouveau style illustration, "
            "gold leaf decorative borders, elegant flowing lines, "
            "Klimt and Mucha inspired, ornate jewel-toned composition, "
            "Pre-Raphaelite beauty, rich gilded aesthetic, "
            "sophisticated decorative art, luminous colors"
        ),
    },
    {
        "name": "renaissance_genius_v1",
        "source": "C:/AI/wisdom/loras/renaissance_genius_training",
        "kohya_dir": "C:/AI/wisdom/loras/renaissance_genius_kohya",
        "output_dir": "C:/AI/wisdom/loras/renaissance_genius_output",
        "concept_name": "20_renaissance_genius",
        "caption": (
            "renaissance_genius drawing, Leonardo da Vinci style sketch, "
            "detailed anatomical study, technical notebook page, "
            "sepia ink on aged parchment, scientific illustration, "
            "mirror writing annotations, Vitruvian precision, "
            "curious inventive spirit, Renaissance master draftsmanship"
        ),
    },
    {
        "name": "vedic_sacred_v1",
        "source": "C:/AI/wisdom/loras/vedic_sacred_training",
        "kohya_dir": "C:/AI/wisdom/loras/vedic_sacred_kohya",
        "output_dir": "C:/AI/wisdom/loras/vedic_sacred_output",
        "concept_name": "20_vedic_sacred",
        "caption": (
            "vedic_sacred painting, Indian miniature art style, "
            "Mughal and Rajput court painting aesthetic, "
            "rich jewel tones saffron gold crimson, "
            "ornate floral borders, sacred spiritual scene, "
            "devotional Hindu temple art, detailed figurative composition, "
            "divine radiance and sacred geometry"
        ),
    },
]

def create_config(lora):
    """Create TOML config for a LoRA."""
    config_path = Path(lora["output_dir"]).parent / f"{lora['name'].replace('_v1','')}_config.toml"

    config = f"""[sdxl_arguments]
cache_text_encoder_outputs = false
no_half_vae = true

[model_arguments]
pretrained_model_name_or_path = "{BASE_MODEL}"
v2 = false
v_parameterization = false

[dataset_arguments]
train_data_dir = "{lora['kohya_dir']}"
resolution = "1024,1024"
enable_bucket = true
min_bucket_reso = 512
max_bucket_reso = 2048
bucket_reso_steps = 64

[training_arguments]
output_dir = "{lora['output_dir']}"
output_name = "{lora['name']}"
save_model_as = "safetensors"
save_every_n_epochs = 1
train_batch_size = 1
max_train_epochs = 4
seed = 42
gradient_checkpointing = true
gradient_accumulation_steps = 4
mixed_precision = "bf16"
full_bf16 = true
max_data_loader_n_workers = 2
persistent_data_loader_workers = true
max_token_length = 225

[optimizer_arguments]
optimizer_type = "AdaFactor"
optimizer_args = ["scale_parameter=False", "relative_step=False", "warmup_init=False"]
learning_rate = 0.0001
lr_scheduler = "cosine_with_restarts"
lr_warmup_steps = 50
lr_scheduler_num_cycles = 3

[network_arguments]
network_module = "networks.lora"
network_dim = 32
network_alpha = 16
network_dropout = 0.1

[caption_arguments]
caption_extension = ".txt"
shuffle_caption = true
keep_tokens = 1
caption_tag_dropout_rate = 0.1

[sample_arguments]
sample_sampler = "euler_a"
sample_every_n_epochs = 1
sample_prompts = "{lora['output_dir']}/sample_prompts.txt"

[advanced_arguments]
sdpa = true
noise_offset = 0.0357
adaptive_noise_scale = 0.00357
multires_noise_iterations = 6
multires_noise_discount = 0.3
min_snr_gamma = 5.0
"""
    config_path.write_text(config)

    # Create sample prompts
    output_dir = Path(lora["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)
    trigger = lora["name"].replace("_v1", "")
    (output_dir / "sample_prompts.txt").write_text(
        f"{trigger} painting, dramatic composition, spiritual atmosphere --w 1024 --h 1024 --l 7 --s 28 --d 42\n"
    )

    return str(config_path)


def prepare_training_dir(lora):
    """Caption images and set up kohya directory."""
    source = Path(lora["source"])
    kohya_dir = Path(lora["kohya_dir"])

    if kohya_dir.exists():
        shutil.rmtree(kohya_dir)

    concept_dir = kohya_dir / lora["concept_name"]
    concept_dir.mkdir(parents=True)

    count = 0
    for f in source.iterdir():
        if f.suffix.lower() in {'.jpg', '.jpeg', '.png', '.tif'}:
            # Copy image
            shutil.copy2(f, concept_dir / f.name)
            # Write caption
            caption_file = concept_dir / f.with_suffix('.txt').name
            caption_file.write_text(lora["caption"], encoding='utf-8')
            count += 1

    return count


def train_lora(lora):
    """Train a single LoRA."""
    name = lora["name"]
    output_dir = Path(lora["output_dir"])
    final_model = output_dir / f"{name}.safetensors"
    deployed = COMFYUI_LORAS / f"{name}.safetensors"

    # Skip if already trained
    if deployed.exists():
        print(f"  SKIP: {name} already deployed at {deployed}")
        return True

    if final_model.exists():
        print(f"  SKIP: {name} already trained, deploying...")
        shutil.copy2(final_model, deployed)
        return True

    # Prepare
    print(f"  Preparing training data...")
    count = prepare_training_dir(lora)
    print(f"  {count} images captioned and copied")

    # Create config
    config_path = lora.get("config") or create_config(lora)
    print(f"  Config: {config_path}")

    # Train
    print(f"  Starting training...")
    result = subprocess.run(
        [PYTHON, "sdxl_train_network.py", "--config_file", config_path],
        cwd=str(KOHYA_DIR),
        env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=False,
    )

    if result.returncode != 0:
        print(f"  FAILED: exit code {result.returncode}")
        return False

    # Deploy
    if final_model.exists():
        shutil.copy2(final_model, deployed)
        print(f"  Deployed to {deployed}")
        return True
    else:
        print(f"  WARNING: final model not found at {final_model}")
        # Try to use last epoch checkpoint
        checkpoints = sorted(output_dir.glob(f"{name}-*.safetensors"))
        if checkpoints:
            shutil.copy2(checkpoints[-1], deployed)
            print(f"  Deployed last checkpoint: {checkpoints[-1].name}")
            return True
        return False


def main():
    print("=" * 60)
    print("  LORA TRAINING MARATHON")
    print("  Training all remaining LoRAs back-to-back")
    print("=" * 60)

    # Check what's already deployed
    deployed = {f.stem for f in COMFYUI_LORAS.glob("*.safetensors")}
    print(f"\nAlready deployed: {deployed}")

    remaining = [l for l in LORA_CONFIGS if l["name"] not in deployed]
    print(f"Remaining to train: {len(remaining)}")

    for i, lora in enumerate(remaining, 1):
        print(f"\n{'=' * 60}")
        print(f"  [{i}/{len(remaining)}] Training: {lora['name']}")
        print(f"{'=' * 60}")

        success = train_lora(lora)

        if success:
            print(f"  DONE: {lora['name']}")
        else:
            print(f"  FAILED: {lora['name']} — continuing to next")

        # Brief pause between trainings to let GPU cool
        if i < len(remaining):
            print(f"\n  Cooling GPU for 30 seconds...")
            time.sleep(30)

    print(f"\n{'=' * 60}")
    print("  ALL LORAS COMPLETE")
    print(f"{'=' * 60}")
    print(f"\nDeployed LoRAs:")
    for f in sorted(COMFYUI_LORAS.glob("*.safetensors")):
        print(f"  {f.name}")


if __name__ == "__main__":
    main()
