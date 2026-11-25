@echo off
chcp 65001 > nul
echo ========================================================
echo   SecureLiveStream 방화벽 규칙 자동 등록 스크립트
echo   (SRT UDP 10000, OBS WebSocket TCP 4455)
echo ========================================================
echo.
echo 관리자 권한을 확인하는 중...
net session >nul 2>&1
if %errorLevel% == 0 (
    echo 관리자 권한 확인 완료.
) else (
    echo [알림] 관리자 권한이 필요합니다.
    echo 이 파일을 우클릭하여 '관리자 권한으로 실행'을 눌러주세요.
    echo.
    pause
    exit /b
)

echo.
echo 방화벽 인바운드 허용 규칙을 등록합니다...
powershell -Command "New-NetFirewallRule -DisplayName 'SecureLiveStream SRT (UDP 10000)' -Direction Inbound -LocalPort 10000 -Protocol UDP -Action Allow -ErrorAction SilentlyContinue"
powershell -Command "New-NetFirewallRule -DisplayName 'OBS WebSocket (TCP 4455)' -Direction Inbound -LocalPort 4455 -Protocol TCP -Action Allow -ErrorAction SilentlyContinue"

echo.
echo ========================================================
echo [완료] SRT(UDP 10000) 및 OBS(TCP 4455) 방화벽 등록 성공!
echo ========================================================
echo.
pause
