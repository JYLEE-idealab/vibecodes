$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$SourceExe = Join-Path $ProjectRoot "target\release\backup-explorer.exe"
if (-not (Test-Path $SourceExe)) {
    Write-Host "빌드된 실행 파일을 찾을 수 없습니다: $SourceExe" -ForegroundColor Red
    Write-Host "먼저 프로젝트 루트에서 'cargo build --release'를 실행해 주세요." -ForegroundColor Yellow
    exit 1
}

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\BackupExplorer"
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

$TargetExe = Join-Path $InstallDir "BackupExplorer.exe"
Copy-Item -Path $SourceExe -Destination $TargetExe -Force

$WshShell = New-Object -ComObject WScript.Shell

# 바탕화면 바로가기 생성
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$DesktopShortcut = $WshShell.CreateShortcut((Join-Path $DesktopPath "백업 & 중복 파일 탐색기.lnk"))
$DesktopShortcut.TargetPath = $TargetExe
$DesktopShortcut.WorkingDirectory = $InstallDir
$DesktopShortcut.Description = "내 PC와 외장하드의 백업 상태 및 중복 파일을 비교하는 탐색기"
$DesktopShortcut.IconLocation = "shell32.dll,45"
$DesktopShortcut.Save()

# 시작 메뉴 바로가기 생성
$StartMenuPath = [Environment]::GetFolderPath("Programs")
$StartShortcut = $WshShell.CreateShortcut((Join-Path $StartMenuPath "백업 & 중복 파일 탐색기.lnk"))
$StartShortcut.TargetPath = $TargetExe
$StartShortcut.WorkingDirectory = $InstallDir
$StartShortcut.Description = "내 PC와 외장하드의 백업 상태 및 중복 파일을 비교하는 탐색기"
$StartShortcut.IconLocation = "shell32.dll,45"
$StartShortcut.Save()

Write-Host "윈도우 설치가 완료되었습니다." -ForegroundColor Green
Write-Host " - 설치 경로: $TargetExe"
Write-Host " - 바탕화면 및 시작 메뉴에 [백업 & 중복 파일 탐색기] 바로가기가 생성되었습니다."
