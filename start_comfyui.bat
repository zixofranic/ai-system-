@echo off
echo Starting ComfyUI...
call C:\Users\ziadf\miniconda3\condabin\conda.bat activate comfyui
cd /d C:\AI\system\ComfyUI
python main.py --port 8188 --preview-method auto
pause
