# TabMonitor Studio

Windows PC와 Android 태블릿(Galaxy Tab 등)을 USB 케이블로 연결하여 초저지연(60fps, NVENC 하드웨어 가속) 16:9 보조 모니터 및 터치스크린으로 활용하는 프로그램입니다.

## 주요 기능
- **초저지연 NVENC 스트리밍**: DXGI Desktop Duplication(`dxcam`) + NVIDIA `h264_nvenc` 하드웨어 인코딩으로 60fps 실시간 전송
- **16:9 고정 화면비 & 자동 회전**: 태블릿 화면에서 정확한 16:9 비율 유지 및 가로 방향 자동 회전(`sensorLandscape`) 지원
- **실시간 해상도 전환**: 작동 중에도 가상 디스플레이 식별자(`\\.\DISPLAYx`) 변경 없이 `1920×1080 (60p)` ↔ `1280×720 (60p)` 즉시 전환
- **1:1 터치 ➔ 마우스 입력 & 커서 렌더링**: 보조 모니터 위에서의 마우스 커서 실시간 표시 및 태블릿 터치 입력 연동
- **양방향 자동 실행**: PC에서 시작 버튼을 누르면 태블릿 앱이 자동 실행되고, 태블릿 앱에서 시작을 누르면 PC 프로그램이 자동으로 열리며 연결됩니다.

## 프로젝트 구조
- `app_gui.py`: Windows용 TabMonitor Studio 메인 GUI 및 스트리밍/터치 서버
- `display_ctl.py`: Windows 가상 디스플레이 해상도 실시간 제어 모듈
- `usb_watcher.py`: 태블릿 앱 실행 감지 및 PC GUI 자동 실행 백그라운드 데몬
- `android/`: Android용 TabMonitor Studio 앱 소스 코드 (`MainActivity.java`)

## 실행 방법
1. Python 패키지 설치:
   ```bash
   pip install -r requirements.txt
   ```
2. `bin/adb.exe` 경로에 Android Platform Tools(ADB)를 배치하고 가상 디스플레이 드라이버(Virtual Display Driver)를 활성화합니다.
3. `python app_gui.py` 또는 `RUN.bat`으로 실행합니다.
