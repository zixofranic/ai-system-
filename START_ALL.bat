@echo off
title Wisdom Pipeline
echo ====================================
echo   AI PRODUCTION STACK
echo ====================================
echo.

REM Check and start Chatterbox
curl -s http://localhost:8004/ >nul 2>&1
if %errorlevel% equ 0 (
    echo [1/4] Chatterbox TTS: already running
) else (
    echo [1/4] Starting Chatterbox TTS...
    start "Chatterbox TTS" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate chatterbox && cd /d C:\AI\system\Chatterbox-TTS-Server && python server.py --port 8004"
    timeout /t 10 /nobreak >nul
)

REM Check and start ComfyUI
curl -s http://localhost:8188/system_stats >nul 2>&1
if %errorlevel% equ 0 (
    echo [2/4] ComfyUI: already running
) else (
    echo [2/4] Starting ComfyUI...
    start "ComfyUI" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate comfyui && cd /d C:\AI\system\ComfyUI && python main.py --port 8188 --preview-method auto"
    timeout /t 10 /nobreak >nul
)

REM Check and start Ollama
curl -s http://localhost:11434/api/tags >nul 2>&1
if %errorlevel% equ 0 (
    echo [3/4] Ollama: already running
) else (
    echo [3/4] Starting Ollama...
    start "Ollama" cmd /k "ollama serve"
    timeout /t 3 /nobreak >nul
)

REM Always start Content Poller (check if already running)
tasklist /FI "WINDOWTITLE eq Content Poller*" 2>nul | find "cmd.exe" >nul
if %errorlevel% equ 0 (
    echo [4/4] Content Poller: already running
) else (
    echo [4/4] Starting Content Poller...
    start "Content Poller" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate chatterbox && cd /d C:\AI\system\scripts && python -u content_poller.py"
)

echo.
echo ====================================
echo   All services ready
echo ====================================
echo.
echo   Chatterbox TTS:  http://localhost:8004
echo   ComfyUI:         http://localhost:8188
echo   Ollama:          http://localhost:11434
echo   Content Poller:  Running
echo.
pause
