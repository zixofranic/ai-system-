@echo off
echo Starting Chatterbox TTS...
call C:\Users\ziadf\miniconda3\condabin\conda.bat activate chatterbox
cd /d C:\AI\system\Chatterbox-TTS-Server
python server.py
pause
