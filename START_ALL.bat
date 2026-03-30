@echo off
echo ====================================
echo   AI PRODUCTION STACK
echo ====================================
echo.

echo [1/4] Starting Chatterbox TTS (Voice Generation)...
start "Chatterbox TTS" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate chatterbox && cd /d C:\AI\system\Chatterbox-TTS-Server && python server.py --port 8004"
timeout /t 5

echo [2/4] Starting ComfyUI (Image Generation)...
start "ComfyUI" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate comfyui && cd /d C:\AI\system\ComfyUI && python main.py --port 8188"
timeout /t 5

echo [3/4] Starting Ollama (LLM)...
start "Ollama" cmd /k "ollama serve"
timeout /t 3

echo [4/4] Starting Content Poller...
start "Content Poller" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate chatterbox && cd /d C:\AI\system\scripts && python content_poller.py"
timeout /t 3

echo.
echo ====================================
echo   All services started
echo ====================================
echo.
echo   Chatterbox TTS:  http://localhost:8004
echo   ComfyUI:         http://localhost:8188
echo   Ollama:          http://localhost:11434
echo   Dashboard:       https://wisdom-dashboard-weld.vercel.app
echo   Content Poller:  Checking Supabase every 5 min
echo.
pause
