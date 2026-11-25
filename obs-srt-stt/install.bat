@echo off
chcp 65001 >nul
echo ========================================================
echo   OBS SRT Stream Live STT Subtitle Plugin - Installer
echo ========================================================
echo.

set "PY311=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"

if exist "%PY311%" (
    echo [INFO] Found Python 3.11 at: %PY311%
) else (
    where python >nul 2>nul
    if %ERRORLEVEL% neq 0 (
        echo [ERROR] Python is not installed or not in PATH.
        echo Please install Python 3.11 ^(64-bit^) from https://www.python.org/downloads/
        pause
        exit /b 1
    )
    set "PY311=python"
)

echo [INFO] Installing required packages ^(faster-whisper, numpy^)...
"%PY311%" -m pip install --upgrade pip
"%PY311%" -m pip install -r "%~dp0requirements.txt"

echo.
echo ========================================================
echo   Installation Complete!
echo   1. Open OBS Studio -^> Tools -^> Scripts -^> Python Settings
echo   2. Set Python Install Path to your Python 3.11 folder:
echo      %LOCALAPPDATA%\Programs\Python\Python311
echo   3. In Scripts tab, click '+' and add obs_srt_stt_plugin.py
echo ========================================================
pause
