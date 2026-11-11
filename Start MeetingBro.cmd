@echo off
setlocal
title MeetingBro
cd /d "%~dp0"

rem Windows entry point. macOS users should double-click Start MeetingBro.command.
where node >nul 2>&1
if errorlevel 1 goto missing_node
where npm >nul 2>&1
if errorlevel 1 goto missing_node
if not exist "app\backend\.venv\Scripts\python.exe" goto missing_backend
if not exist "app\frontend\node_modules\electron\path.txt" goto missing_frontend

rem Keep models local and prevent automatic resource downloads.
set "HF_HOME=%CD%\models\huggingface"
set "ORT_DISABLE_TELEMETRY=1"
set "HF_HUB_OFFLINE=1"
set "TRANSFORMERS_OFFLINE=1"
set "HF_HUB_DISABLE_TELEMETRY=1"
rem Metal is macOS-only. Let the Windows runtime select its supported engine.
set "MEETINGBRO_WHISPER_ENGINE=auto"
set "MEETINGBRO_WHISPER_DEVICE=auto"

echo Starting MeetingBro and its managed backend...
pushd "app\frontend"
call npm run dev
set "RESULT=%ERRORLEVEL%"
popd
if not "%RESULT%"=="0" pause
exit /b %RESULT%

:missing_node
echo Node.js and npm are required. No downloads were started.
pause
exit /b 1

:missing_backend
echo Install the backend in a Windows Python environment first.
echo The macOS .venv cannot be used on Windows.
pause
exit /b 1

:missing_frontend
echo Frontend dependencies are missing. No downloads were started.
pause
exit /b 1
