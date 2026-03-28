"""
Watch for romantic_landscape LoRA to complete, then train remaining 4.
Run this and walk away — it handles everything.
"""
import time
import subprocess
import shutil
from pathlib import Path

COMFYUI_LORAS = Path("C:/AI/system/ComfyUI/models/loras")
ROMANTIC_OUTPUT = Path("C:/AI/wisdom/loras/romantic_landscape_output")
PYTHON = "C:/Users/ziadf/miniconda3/envs/lora_train/python.exe"

print("Watching for romantic_landscape completion...")
print("Checking every 60 seconds...")

while True:
    final = ROMANTIC_OUTPUT / "romantic_landscape_v1.safetensors"
    checkpoints = list(ROMANTIC_OUTPUT.glob("romantic_landscape_v1-*.safetensors"))

    if final.exists():
        print(f"\nRomantic landscape DONE! Deploying...")
        shutil.copy2(final, COMFYUI_LORAS / "romantic_landscape_v1.safetensors")
        print("Deployed. Starting remaining LoRAs...")
        break
    elif len(checkpoints) >= 3:
        # If we have 3+ checkpoints but no final, it might be wrapping up
        print(f"  {len(checkpoints)} checkpoints found, waiting for final...")

    time.sleep(60)

# Now train the remaining 4
print("\n" + "=" * 60)
print("Starting LoRA marathon — remaining 4 styles")
print("=" * 60)

result = subprocess.run(
    [PYTHON, "C:/AI/system/scripts/train_all_loras.py"],
    env={**__import__('os').environ, "PYTHONIOENCODING": "utf-8"},
)

print(f"\nMarathon finished with exit code {result.returncode}")
print("All LoRAs should now be deployed to ComfyUI!")
