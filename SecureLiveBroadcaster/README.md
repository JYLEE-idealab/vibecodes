# SecureLiveBroadcaster (SRT 모바일 라이브 방송 솔루션)

> **외장 액션캠(DJI 오즈모 액션 5 / UVC) 및 스마트폰 내장 카메라를 활용한 초저지연 SRT 라이브 스트리밍 안드로이드 앱 통합 프로젝트**

삼성 갤럭시(One UI) 등 안드로이드 단말기의 제한적인 외부 카메라 HAL 제약을 NDK 유저스페이스 드라이버(`UVCAndroid`)로 극복하고, RootEncoder 기반의 안정적인 H.264 하드웨어 인코딩 및 MPEG-TS 패킷화 기술을 결합하여 OBS Studio로 1080p 고화질 영상을 실시간 무선 전송하는 전문 방송용 솔루션입니다.

---

## 🌟 핵심 기능 및 특징

1. **외장 액션캠(UVC) & 스마트폰 내장 카메라 원터치 전환**:
   * C to C 케이블로 연결된 오즈모 액션 5 등 UVC 비디오 장치를 NDK 레벨에서 직접 제어.
   * 스마트폰 내장 카메라(전/후면)와 외장 액션캠 간 실시간 끊김 없는 입력 소스 스위칭.
2. **초저지연 1080p 5 Mbps 고화질 방송 파이프라인**:
   * MediaCodec 하드웨어 가속 인코딩(`bitrate = 5 Mbps`, `fps = 30`, `iFrameInterval = 2`).
   * OBS 즉각 수신을 위한 SPS/PPS 헤더 자동 관리 및 H.264 Annex-B 패킷 인젝션.
   * 무선 Wi-Fi / Tailscale VPN 상에서 패킷 손실 없는 **SRT(Secure Reliable Transport)** 프로토콜 송출.
3. **듀얼 서피스 렌더링 (실시간 뷰파인더 모니터링)**:
   * 송출 중에도 스마트폰 화면에서 딜레이 없이 카메라 앵글을 확인할 수 있는 OpenGL TextureView 기반 프리뷰 제공.
4. **방송 편의 기능 완비**:
   * **치지직(Chzzk) 연동**: 실시간 채팅 수신 및 TTS 음성 후원 알림 지원.
   * **OBS WebSocket 원격 제어**: 방송 시작/종료, 씬 전환 연동.
   * **오작동 방지**: 방송 중 주머니 터치 방지(Touch Lock) 및 Foreground Service 무중단 백그라운드 송출.
5. **보안 지향 설계**:
   * 불필요한 위치/저장소 권한 원천 배제.
   * Tailscale(`100.x.x.x`) 및 로컬 사설망 IP 검증으로 무단 외부 유출 방지.

---

## 📁 디렉터리 구성

```text
SecureLiveBroadcaster/
├── android/                         # 안드로이드 스튜디오 프로젝트 (클린 소스코드)
│   ├── app/                         # 핵심 애플리케이션 모듈 (Kotlin + NDK)
│   │   ├── src/main/java/com/example/securelivestreamingapp/
│   │   │   ├── MainActivity.kt               # 메인 방송 UI 및 상태 제어
│   │   │   ├── UvcCameraManager.kt           # UVC 및 내장 카메라 파이프라인 총괄
│   │   │   ├── CameraUvcSource.kt            # UVCAndroid NDK <-> RootEncoder 브릿지
│   │   │   ├── StreamingManager.kt           # SRT 스트림 전송 매니저
│   │   │   ├── StreamingForegroundService.kt # 백그라운드 무중단 방송 서비스
│   │   │   ├── ChzzkChatManager.kt           # 치지직 실시간 채팅 연동
│   │   │   ├── ObsWebSocketManager.kt        # OBS 원격 제어
│   │   │   └── SecurityManager.kt            # IP 보안 검증
│   │   └── build.gradle.kts
│   ├── gradle/                      # Gradle Wrapper v8.x
│   └── build.gradle.kts             # 루트 빌드 스크립트
├── release/                         # 즉시 설치 및 실행 가능한 배포 아티팩트
│   ├── SecureLiveStreamingApp_Universal_Release.apk # v4.0 완제품 APK
│   └── 방화벽_포트_자동허용.bat                  # PC 방화벽 원클릭 설정 스크립트
├── docs/                            # 상세 문서 및 공모전 보고서
│   ├── 사용설명서_및_설정가이드.txt            # 상세 앱 조작 및 OBS 세팅 매뉴얼
│   ├── PROJECT_HISTORY.md           # 0~8Kbps 버그 및 모자이크 현상 해결 히스토리
│   └── 공모전_출품서_및_작품설명.md          # 인공지능 활용 우수사례 공모전 제출서
└── README.md                        # 본 문서
```

---

## 🚀 빠른 시작 가이드

### 1. APK 즉시 설치 (개발 환경 없이 바로 사용)
`release` 디렉터리에 포함된 빌드 완료 APK를 안드로이드 스마트폰에 설치합니다:

```bash
adb install -r release/SecureLiveStreamingApp_Universal_Release.apk
```

### 2. PC OBS Studio 설정
1. OBS Studio 실행 ➔ 소스 목록 ➔ **[미디어 소스]** 추가.
2. `로컬 파일` 체크 **해제**.
3. **입력(Input)** 필드에 입력:
   ```text
   srt://0.0.0.0:10000?mode=listener
   ```
4. **입력 형식**: `mpegts` 입력 후 확인.

### 3. PC 방화벽 허용 (필요 시)
`release/방화벽_포트_자동허용.bat`을 마우스 우클릭하여 **[관리자 권한으로 실행]**하면 UDP 10000번 포트가 원클릭으로 개방됩니다.

### 4. 방송 시작
1. 오즈모 액션 5의 USB 설정을 **UVC / 웹캠 모드**로 변경 후 스마트폰에 C to C 케이블로 연결.
2. 스마트폰 앱 실행 ➔ 상단 `[내장 카메라]` 버튼을 눌러 `[외장 UVC]`로 전환.
3. PC의 Tailscale IP(예: `100.x.x.x`) 입력 후 **[방송 시작]** 버튼 터치!
