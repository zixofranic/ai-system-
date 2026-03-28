@echo off
echo ====================================
echo   AI PRODUCTION STACK
echo ====================================
echo.

echo [1/3] Starting ComfyUI (Image Generation)...
start "ComfyUI" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate comfyui && cd /d C:\AI\system\ComfyUI && python main.py --port 8188"
timeout /t 5

echo [2/3] Starting Ollama (LLM)...
start "Ollama" cmd /k "ollama serve"
timeout /t 3

echo [3/3] Starting Content Poller...
start "Content Poller" cmd /k "call C:\Users\ziadf\miniconda3\condabin\conda.bat activate chatterbox && cd /d C:\AI\system\scripts && python content_poller.py"
timeout /t 3

echo.
echo ====================================
echo   All services starting...
echo ====================================
echo.
echo   ComfyUI:         http://localhost:8188
echo   Ollama:          http://localhost:11434
echo   n8n (VPS):       http://107.173.231.158:5678
echo   Dashboard:       https://wisdom-dashboard-weld.vercel.app
echo.
echo   Content Poller:  Checking Supabase every 5 min
echo.
pause
