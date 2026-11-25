# 1st project 📡 SecureLiveBroadcaster

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Android-3DDC84?style=for-the-badge&logo=android&logoColor=white" />
  <img src="https://img.shields.io/badge/Language-Kotlin-7F52FF?style=for-the-badge&logo=kotlin&logoColor=white" />
  <img src="https://img.shields.io/badge/Protocol-SRT%20%7C%20MPEG--TS-FF6F00?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Video-1080p%205Mbps%20H.264-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" />
</p>

<p align="center">
  <strong>외장 액션캠(DJI 오즈모 액션 5 / UVC) 및 내장 카메라를 활용한 초저지연(Sub-second) SRT 라이브 스트리밍 안드로이드 솔루션</strong>
</p>

---

## 💡 프로젝트 소개

**SecureLiveBroadcaster**는 야외 1인 방송인을 위해 수백만 원대의 전용 방송 장비(LiveU 등) 없이, **스마트폰과 액션캠 1대만으로 스튜디오급 고화질 생방송을 송출**할 수 있도록 설계된 오픈소스 라이브 스트리밍 앱입니다.

특히 삼성 갤럭시(One UI) 등 국내 주요 기기에서 제조사 HAL 제약으로 인해 USB 웹캠이 인식되지 않던 문제를 **NDK 유저스페이스 UVC 드라이버**로 해결하였으며, OBS Studio 연동 시 발생하는 디코딩 지연과 검은 화면을 방지하기 위해 **MPEG-TS 패킷화 및 H.264 Baseline 인코딩 파이프라인**을 완벽히 구축했습니다.

---

## ✨ 핵심 기능

- **🎬 하이브리드 비디오 입력 (UVC 외장 캠 + 스마트폰 내장 카메라)**:
  - C to C 케이블로 연결된 외장 액션캠(DJI 오즈모 액션 등)을 드라이버 없이 자동 인식.
  - 방송 중에도 스마트폰 내장 렌즈(전/후면)와 외장 액션캠 간 실시간 원터치 전환 지원.
- **⚡ 초저지연 SRT(Secure Reliable Transport) 프로토콜**:
  - 패킷 손실이 잦은 야외 LTE/5G 및 Wi-Fi 환경에서도 1초 미만의 지연시간(Sub-second Latency) 유지.
  - Tailscale VPN(`100.x.x.x`) 및 로컬 사설망을 통한 안전한 비공개 전송.
- **💎 1080p Full HD @ 5 Mbps 고화질 인코딩**:
  - MediaCodec 하드웨어 가속 인코딩(`bitrate = 5 Mbps`, `fps = 30`, `iFrameInterval = 2`).
  - 매 키프레임마다 SPS/PPS 파라미터 세트를 강제 주입하여 OBS 화면 즉각 복구 및 깍두기(모자이크) 현상 원천 차단.
- **📱 듀얼 서피스 실시간 뷰파인더**:
  - 인코더로 프레임이 전송되는 동시에 폰 화면에 지연 없는 모니터링 프리뷰 제공.
- **🎙️ 1인 방송 특화 편의 기능**:
  - **치지직(Chzzk) 실시간 채팅창 및 후원 TTS 연동**.
  - **OBS WebSocket 지원**으로 스마트폰에서 방송 시작/종료 및 씬(Scene) 제어.
  - 화면 주머니 터치 오작동 방지(Touch Lock) 및 Foreground Service 무중단 백그라운드 송출.

---

## 🏗️ 시스템 아키텍처

```text
[비디오 소스 (내장 카메라 / 오즈모 액션5 UVC)]
                     ↓
[UVCAndroid (NDK Libuvc) / Camera2 API]
          ↙                                ↘
[OpenGL SurfaceTexture (실시간 뷰파인더)]     [MediaCodec (H.264 하드웨어 인코더)]
                                                    ↓ (SPS/PPS Header Injection)
                                        [RootEncoder (MPEG-TS Muxer)]
                                                    ↓ (1316-byte Chunking)
                                        [SRT Socket Streamer (Caller Mode)]
                                                    ↓ (Wi-Fi / Tailscale VPN)
                                        [PC OBS Studio (SRT Listener)]
```

---

## 🚀 빠른 시작 (Quick Start)

### 1. 앱 설치 (사용자)
복잡한 빌드 과정 없이 `release/` 폴더에 포함된 완제품 APK를 설치할 수 있습니다:

```bash
adb install -r release/SecureLiveStreamingApp_Universal_Release.apk
```

### 2. OBS Studio 수신 설정
1. OBS Studio 실행 ➔ **[소스 목록]** ➔ **[미디어 소스]** 추가.
2. `로컬 파일` 체크 **해제**.
3. **입력(Input)** 필드:
   ```text
   srt://0.0.0.0:10000?mode=listener
   ```
4. **입력 형식**: `mpegts` 입력 후 확인.

### 3. PC 방화벽 설정 (Windows)
PC에서 UDP 10000번 포트가 차단되어 스트림이 수신되지 않을 경우, `release/방화벽_포트_자동허용.bat` 파일을 **[관리자 권한으로 실행]**하면 1초 만에 방화벽 규칙이 등록됩니다.

### 4. 방송 시작
1. 오즈모 액션의 설정을 **[UVC / 웹캠 모드]**로 변경 후 스마트폰에 연결합니다.
2. 앱 실행 후 PC의 IP(Tailscale 사설망 IP 권장) 및 포트 `10000`을 확인합니다.
3. **[방송 시작]** 버튼을 누르면 OBS에 실시간 1080p 방송이 즉시 송출됩니다.

---

## 📂 프로젝트 구조

```text
SecureLiveBroadcaster/
├── android/                         # 안드로이드 스튜디오 프로젝트 소스코드
│   ├── app/                         # 핵심 애플리케이션 모듈 (Kotlin + NDK)
│   │   ├── src/main/java/com/example/securelivestreamingapp/
│   │   │   ├── MainActivity.kt               # 메인 UI 및 이벤트 제어
│   │   │   ├── UvcCameraManager.kt           # UVC/내장 렌즈 파이프라인 관리
│   │   │   ├── CameraUvcSource.kt            # UVCAndroid NDK ↔ RootEncoder 브릿지
│   │   │   ├── StreamingManager.kt           # SRT 소켓 전송 매니저
│   │   │   ├── StreamingForegroundService.kt # 백그라운드 무중단 서비스
│   │   │   ├── ChzzkChatManager.kt           # 치지직 실시간 채팅 연동
│   │   │   └── ObsWebSocketManager.kt        # OBS 원격 제어
│   │   └── build.gradle.kts
│   └── gradle/                      # Gradle Wrapper v8.x
├── release/                         # 완제품 APK 및 윈도우 방화벽 스크립트
├── docs/                            # 상세 가이드 및 공모전 보고서
└── README.md                        # 본 소개 문서
```

---

## 🛠️ 개발 환경 및 빌드 방법 (개발자용)

* **IDE**: Android Studio Hedgehog (2023.1.1) 이상 권장
* **Build System**: Gradle 8.2 (Kotlin DSL)
* **Target SDK**: `34` (Android 14) / **Min SDK**: `26` (Android 8.0)
* **NDK Support**: `arm64-v8a`, `armeabi-v7a`

```bash
# 레포지토리 클론 후 안드로이드 프로젝트 빌드
cd android
./gradlew assembleRelease
```

---

## 🏆 성과 및 수상
* **제2회 인공지능 활용 우수사례 공모전** 출품작
* 생성형 AI(Google DeepMind Antigravity)와의 심층 페어 프로그래밍을 통해 시스템 레벨(NDK, 하드웨어 코덱, 저지연 네트워크 프로토콜)의 기술적 난제를 단기간에 극복한 융합 개발 사례

---

## 📄 라이선스 (License)
본 프로젝트는 [MIT License](LICENSE)를 따릅니다.
