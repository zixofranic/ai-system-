@echo off
echo Starting Content Poller...
echo Checks Supabase every 5 minutes for queued content.
echo DO NOT CLOSE THIS WINDOW.
echo.
cd C:\AI\system\scripts
C:\Users\ziadf\miniconda3\envs\chatterbox\python.exe content_poller.py
pause
