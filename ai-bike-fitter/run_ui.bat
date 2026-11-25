@echo off
chcp 65001 > nul
cd /d "%~dp0"
title 🚴 AI 자전거 피팅 실시간 동영상 합성 & 웹캠 라이브 레코더

echo ============================================================================
echo  🚴 AI 자전거 피팅 실시간 동영상 합성 ^& 웹캠 라이브 레코더
echo ============================================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [❌ 오류] 이 PC에 Python이 설치되어 있지 않거나 PATH에 등록되지 않았습니다.
    echo  - https://www.python.org/downloads/ 에서 Python 3.10 ~ 3.12를 설치해주세요.
    echo  - 설치 시 첫 화면 하단의 "Add python.exe to PATH" 체크박스를 반드시 체크해주세요!
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 이 PC 전용 Python 가상환경(.venv)을 처음 생성하고 있습니다...
    python -m venv .venv
    echo [2/3] 필요한 AI 및 영상 합성 라이브러리를 자동 설치합니다 (약 1~3분)...
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\pip.exe install -r requirements_portable.txt
) else (
    .\.venv\Scripts\python.exe -c "import gradio, ultralytics, cv2, PIL" >nul 2>nul
    if %errorlevel% neq 0 (
        echo [안내] 현재 PC 환경에 맞게 가상환경과 라이브러리를 재설정합니다...
        rmdir /s /q .venv
        python -m venv .venv
        .\.venv\Scripts\python.exe -m pip install --upgrade pip
        .\.venv\Scripts\pip.exe install -r requirements_portable.txt
    )
)

echo.
echo [3/3] 🚀 AI 자전거 피팅 웹앱 서버를 시작합니다! (http://127.0.0.1:7861)
echo ============================================================================
start "" "http://127.0.0.1:7861"
.\.venv\Scripts\python.exe main.py --ui --port 7861
pause
