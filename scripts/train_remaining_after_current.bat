@echo off
echo Waiting for current LoRA to finish...
echo When romantic_landscape completes, this will train the remaining 4.
echo.
echo DO NOT CLOSE THIS WINDOW.
echo.

:wait_loop
nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader > temp_gpu.txt
set /p GPU_UTIL=<temp_gpu.txt
del temp_gpu.txt
echo GPU: %GPU_UTIL%

REM Check if the current training output exists (final model)
if exist "C:\AI\wisdom\loras\romantic_landscape_output\romantic_landscape_v1.safetensors" (
    echo Romantic landscape done! Starting remaining LoRAs...
    goto start_remaining
)

timeout /t 60 /nobreak > nul
goto wait_loop

:start_remaining
REM Deploy romantic landscape
copy "C:\AI\wisdom\loras\romantic_landscape_output\romantic_landscape_v1.safetensors" "C:\AI\system\ComfyUI\models\loras\"
echo Romantic landscape deployed.

REM Train all remaining
cd C:\AI\system\scripts
C:\Users\ziadf\miniconda3\envs\lora_train\python.exe train_all_loras.py

echo.
echo ALL LORAS COMPLETE!
pause
