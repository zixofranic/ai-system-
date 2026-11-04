@echo off
REM Launcher for the daily automation tasks. Fired by Task Scheduler:
REM   AI_DailyAutoGen        5:00 AM  run_daily_automation.bat daily_autogen
REM   AI_DailyPublishDigest  8:30 AM  run_daily_automation.bat daily_publish_digest
REM Uses PowerShell for the timestamp because %date% is locale-dependent.

setlocal EnableExtensions EnableDelayedExpansion
set "JOB=%~1"
set "PYTHON=C:\Users\ziadf\miniconda3\envs\chatterbox\python.exe"
set "SCRIPT_DIR=C:\AI\system\scripts"
set "LOG_DIR=C:\AI\system\scripts\logs"

if "%JOB%"=="" (
  echo Usage: run_daily_automation.bat daily_autogen^|daily_publish_digest
  endlocal & exit /b 12
)
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f "usebackq tokens=*" %%i in (`powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HHmmss"`) do set "TS=%%i"
if "!TS!"=="" set "TS=unknown_ts"
set "LOG=%LOG_DIR%\%JOB%_!TS!.log"

echo === %JOB% run: !TS! === > "!LOG!"
if not exist "%PYTHON%" (
  echo FATAL: Python not found at %PYTHON% >> "!LOG!"
  endlocal & exit /b 10
)

cd /d "%SCRIPT_DIR%"
"%PYTHON%" %JOB%.py >> "!LOG!" 2>&1
set "RC=!ERRORLEVEL!"
echo === Exit code: !RC! === >> "!LOG!"

endlocal & exit /b %RC%
