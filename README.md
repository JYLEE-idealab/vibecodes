<div align="center">


# 1st project 📡 SecureLiveBroadcaster

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Android-3DDC84?style=for-the-badge&logo=android&logoColor=white" />
  <img src="https://img.shields.io/badge/Language-Kotlin-7F52FF?style=for-the-badge&logo=kotlin&logoColor=white" />
  <img src="https://img.shields.io/badge/Protocol-SRT%20%7C%20MPEG--TS-FF6F00?style=for-the-badge" />
  <img src="https://img.shields.io/badge/Video-1080p%205Mbps%20H.264-blue?style=for-the-badge" />
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" />
</p>

</div>

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

<div align="center">

# 2nd project 🕺 Idol Choreography Synchronization Analyzer (`idol-choreo-sync`)

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![YOLOv8-Pose](https://img.shields.io/badge/YOLOv8--Pose-Ultralytics-00D2FF)
![ByteTrack](https://img.shields.io/badge/Multi--Object_Tracking-ByteTrack-38EF7D)
![OpenCV](https://img.shields.io/badge/OpenCV-Video_Synthesis-5C3EE8?logo=opencv&logoColor=white)
![Gradio](https://img.shields.io/badge/Web_UI-Gradio-FF7C00)

</div>


**아이돌 그룹의 안무 연습 영상(Dance Practice)이나 직캠(Full Cam)을 넣으면, 다중 인물을 실시간 트래킹하여 멤버 간 안무 일치율(칼군무 싱크로율)을 계산하고 원본 동영상(오디오 포함) 위에 실시간 HUD 그래픽을 합성해 주는 AI 프로그램**입니다.

---

## ✨ 주요 기능 (Key Features)

1. **다중 인물 실시간 트래킹 (`YOLOv8-Pose` + `ByteTrack`)**
   * 화면 속 여러 명의 멤버를 동시에 감지하고 고유 ID(`ID #1`, `ID #2`...)를 부여하여 동선이 이동해도 각 멤버별 평균 안무 정확도를 끝까지 추적합니다.
   * 배경에 작게 비치는 스태프나 거울 반사 노이즈를 바운딩 박스 면적 비율 필터로 자동 제거합니다.

2. **키·거리 차이에 영향받지 않는 공정한 칼군무 알고리즘**
   * 멤버마다 신장이 다르거나 앞줄/뒷줄(카메라와의 거리)에 서 있어도 공정하게 비교할 수 있도록 단순 픽셀 좌표가 아닌 아래 두 가지 기하학적 특징을 결합합니다:
     * **사지 단위 방향 벡터 코사인 유사도 (Limb Unit-Vector Cosine Similarity - 70% 가중치):** 양팔(상완/전완), 양다리(대퇴/하퇴), 어깨선, 몸통 등 11개 신체 부위의 2D 방향 벡터 합의도 측정
     * **관절 내부 사이각 일치도 (Interior Joint Angle Agreement - 30% 가중치):** 팔꿈치, 어깨, 무릎, 고관절 등 8대 주요 관절의 굽힘 각도 편차 측정

3. **원본 영상 + 오디오 위 실시간 HUD 오버레이 `.MP4` 합성**
   * **부위별 색상 스켈레톤:** 그룹 평균 동작과 일치하는 부위는 **🟢 네온 그린(`85% 이상`)**, 미세 오차는 **🟡 옐로우(`65~85%`)**, 혼자 각도가 틀어진 팔/다리는 **🔴 코랄 레드(`65% 미만`)**로 즉시 시각화됩니다.
   * **멤버별 머리 위 배지:** 각 멤버 상단에 `[ID #1 | 94%]` 형태로 개인별 실시간 일치율을 표시합니다.
   * **상단 싱크로율 대시보드 & 하단 라이브 파형 그래프:** 현재 프레임의 그룹 전체 칼군무 게이지 바와 최근 6초간의 실시간 싱크로율 변화 곡선(Waveform)을 영상 하단에 함께 렌더링하며, `ffmpeg` 연동으로 **원본 음악(오디오 트랙)**을 그대로 유지합니다.

4. **원하는 구간(`시작 시간 ~ 종료 시간`) 지정 분석 & 리포트 차트 생성**
   * 영상을 업로드하면 전체 길이를 자동 감지하며, 원하는 하이라이트 안무 구간만 지정해 빠르게 합성할 수 있습니다.
   * 분석 완료 시 **시간대별 칼군무 싱크로율 그래프 & 멤버별 평균 일치율 랭킹 차트(`.PNG`)**와 하이라이트/오차 주의 타임스탬프 리포트를 자동 출력합니다.

---

## 🚀 설치 및 실행 방법 (Quick Start)

### 방법 1. 윈도우 원클릭 실행 (`run_ui.bat` 권장)
1. 저장소를 다운로드(또는 Clone)한 뒤 폴더 안의 **`run_ui.bat`** 파일을 더블클릭합니다.
2. 첫 실행 시 자동으로 가상환경(`.venv`) 생성 및 필수 라이브러리를 설치한 후 브라우저(`http://127.0.0.1:7860`)가 열립니다.
3. 두 번째 실행부터는 설치 과정 없이 2~3초 만에 즉시 실행됩니다.

### 방법 2. 터미널 수동 실행 (Web UI & CLI)
```bash
# 1. 가상환경 생성 및 패키지 설치
python -m venv .venv
.\.venv\Scripts\pip install -r requirements_portable.txt

# 2-A. 브라우저 웹 UI 모드로 실행 (http://127.0.0.1:7860)
.\.venv\Scripts\python main.py --ui

# 2-B. CLI 명령어로 특정 구간(예: 15초~45초) 바로 분석 및 합성
.\.venv\Scripts\python main.py --input dance_practice.mp4 --start 15 --end 45 --output outputs
```

---

## 📂 폴더 구조 (Project Structure)

```text
idol-choreo-sync/
 ├── main.py                     # Gradio 웹 UI 대시보드 & CLI 실행 엔트리포인트
 ├── analyzer.py                 # YOLOv8-Pose + ByteTrack 칼군무 일치율 계산 및 HUD 영상 합성 엔진
 ├── demo_test.py                # 4인조 아이돌 안무 시뮬레이션 데모 생성 및 테스트 스크립트
 ├── run_ui.bat                  # Windows 원클릭 가상환경 세팅 & 웹 UI 자동 실행기
 ├── requirements_portable.txt   # 핵심 의존성 패키지 목록
 └── outputs/                    # 합성 완료된 .MP4 동영상 및 리포트 차트(.PNG) 저장 폴더
```

---

<div align="center">

# 3rd project 🚴 AI Bike Fitting Studio (`ai-bike-fitter`)

![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)
![YOLOv8-Pose](https://img.shields.io/badge/YOLOv8--Pose-Biomechanics-00D2FF)
![OpenCV](https://img.shields.io/badge/OpenCV-Marker_Snap_%26_HUD-38EF7D?logo=opencv&logoColor=white)
![Gradio](https://img.shields.io/badge/Web_UI-Live_Webcam_%26_MP4-FF7C00)

</div>


**인도어 트레이너(로라)에 자전거를 고정하고 측면 또는 정면에서 촬영한 동영상(또는 실시간 웹캠)을 입력하면, 라이더의 5대 핵심 피팅 관절 각도를 실시간 추적하여 `Crank At 6 O'Clock` 바이오메카닉 폴리곤과 5단계 색깔별 기준 게이지(`빨-노-초-노-빨`)를 원본 동영상 위에 그대로 합성(`.MP4`)해 주는 AI 자전거 피팅 프로그램**입니다.

---

## ✨ 주요 기능 (Key Features)

1. **`Crank At 6 O'Clock` 블루 폴리곤 & 5단계 색깔별 기준 게이지 오버레이**
   * 페달링 매 프레임마다 어깨 - 팔꿈치 - 골반 - 무릎 - 발목 - 발끝을 연결하는 **반투명 시안-블루 바이오메카닉 폴리곤 메쉬**를 영상 위에 합성합니다.
   * 5대 핵심 피팅 각도 카드(**`Shoulder Angle`**, **`Torso Angle`**, **`Hip Angle`**, **`Knee Angle`**, **`Foot / Elbow Angle`**)마다 하단에 **`[🔴 빨강 | 🟡 노랑 | 🟢 초록(권장) | 🟡 노랑 | 🔴 빨강]` 5칸 컬러 게이지 바와 현재 내 각도 위치(흰색 포인터 핀)**를 실시간 표시합니다.
   * 우측 하단에 페달링에 따른 **실시간 무릎 각도 파형 그래프(Live Waveform)**와 초록색 권장 하사점 밴드(`140°~150°`)를 함께 렌더링합니다.

2. **🎯 관절·클릿 슈즈 7곳 원형 컬러 스티커 정밀 스냅 엔진 (`±0.5°` 프로 피팅샵 수준 정밀도)**
   * 다이소 원형 컬러 스티커(노란색/빨간색/형광연두, 지름 1.5~2.5cm)를 7곳의 관절 회전축 및 클릿 슈즈 발볼 옆면에 부착하고 촬영하면, AI 관절 위치 주변에서 **실제 스티커의 원 중심점(Sub-pixel Centroid)으로 좌표를 자석처럼 고정(Snap)**합니다.
   * 검은색 빕숏 착용 시 발생하는 AI 관절 떨림을 제거하고, 일반 AI가 인식하지 못하는 **클릿 슈즈 페달 중심축(발 각도 `Foot Angle` & `3시 KOPS`)**까지 100% 정밀하게 측정합니다.
   * 웹앱 화면 내에 **7곳 해부학적 부착 위치 도해 이미지(`sticker_placement_guide.png`) 및 상세 가이드 표**가 기본 내장되어 있습니다.

3. **📹 실시간 웹캠 라이브 피팅 & 자동 오버레이 `.MP4` 녹화 (Tab 2)**
   * **방식 A (브라우저 실시간 스트리밍 녹화):** 웹캠을 켜고 내 자세 위에 입혀진 각도 폴리곤과 컬러 게이지를 실시간 모니터로 확인하면서 `[🔴 실시간 녹화 시작]` ~ `[⏹️ 녹화 종료]`를 누르면 즉시 합성된 `.MP4` 동영상과 리포트가 생성됩니다.
   * **방식 B (USB/노트북 웹캠 720p 고프레임 직접 녹화):** 브라우저 전송 지연 없이 OpenCV 초저지연 팝업 모니터 창을 띄워 고화질로 자동 녹화 및 `.MP4` 인코딩을 수행합니다.

4. **6시 하사점(`Crank At 6 O'Clock`) & 12시 상사점 자동 포착 + 맞춤형 처방전**
   * 페달이 가장 아래로 내려간 **6시 하사점(BDC - 안장 높이 핵심 기준)**과 가장 위로 올라온 **12시 상사점(TDC - 고관절 각도 기준)** 프레임을 자동 감지하여 고화질 스냅샷 이미지(`.PNG`)로 캡처합니다.
   * 선택한 라이딩 스타일(**`로드 엔듀런스`** / **`로드 레이싱`** / **`에어로·TT`** / **`MTB·그래블`**)에 맞춰 안장 높이 조정량(`mm` 단위), 스템 리치, 핸들바 낙차, 정면 11자 무릎 정렬(클릿 웨지) 처방전을 자동 출력합니다.

---

## 📍 오차를 ±0.5°로 줄이는 원형 스티커 7곳 부착 위치

| 번호 | 부착 부위 | 정확한 해부학적 회전축 (뼈 위치) | 측정 효과 |
| :---: | :--- | :--- | :--- |
| **①** | **손목 (Wrist)** | 손목 바깥쪽 복숭아뼈 (척골 경상돌기 / 장갑 위) | 팔꿈치 굽힘각(Elbow Angle) 측정 |
| **②** | **팔꿈치 (Elbow)** | 팔꿈치 바깥쪽 볼록한 뼈 (상완골 외측상과) | 어깨 리치 & 팔꿈치 충격 흡수 각도 |
| **③** | **어깨 (Shoulder)** | 어깨봉우리 뼈 (견봉, Acromion) 바로 아래 회전 중심 | 상체 숙임각(Torso) & 어깨 리치 각도 |
| **④★** | **골반 (Hip - 대전자)** | **고관절 대전자 (다리를 들 때 허벅지 옆에서 구르는 큰 뼈)** | 무릎·고관절·상체 각도의 핵심 기준축 |
| **⑤★** | **무릎 (Knee)** | **대퇴골 외측상과 (무릎 앞 뚜껑뼈가 아닌 옆면 회전축)** | **6시 하사점 무릎 각도(안장 높이) 핵심 기준** |
| **⑥★** | **발목 (Ankle)** | **바깥쪽 복사뼈 정중앙 (외과, Lateral Malleolus)** | 무릎 각도 및 페달링 앵클링(Ankling) 측정 |
| **⑦★** | **클릿 슈즈 앞코** | **제5중족골 헤드 (슈즈 보아 다이얼 아래 발볼 페달 중심축)** | **발 각도(Foot Angle) & 3시 KOPS 수직선 정밀 측정** |

---

## 🚀 설치 및 실행 방법 (Quick Start)

### 방법 1. 윈도우 원클릭 실행 (`run_ui.bat` 권장)
1. 저장소를 다운로드(또는 Clone)한 뒤 폴더 안의 **`run_ui.bat`** 파일을 더블클릭합니다.
2. 첫 실행 시 자동으로 가상환경(`.venv`) 생성 및 필수 라이브러리를 설치한 후 브라우저(`http://127.0.0.1:7861`)가 열립니다.
3. 두 번째 실행부터는 설치 과정 없이 2~3초 만에 즉시 실행됩니다.

### 방법 2. 터미널 수동 실행 (Web UI & CLI)
```bash
# 1. 가상환경 생성 및 패키지 설치
python -m venv .venv
.\.venv\Scripts\pip install -r requirements_portable.txt

# 2-A. 브라우저 웹 UI 모드로 실행 (http://127.0.0.1:7861)
.\.venv\Scripts\python main.py --ui

# 2-B. CLI 명령어로 동영상 구간(예: 5초~15초) 바로 합성
.\.venv\Scripts\python main.py --input roller_side.mp4 --start 5 --end 15 --output outputs
```

---

## 📂 폴더 구조 (Project Structure)

```text
ai-bike-fitter/
 ├── main.py                     # Gradio 웹 UI (Tab 1: 동영상 업로드 합성 / Tab 2: 실시간 웹캠 녹화) & CLI
 ├── bike_fit_analyzer.py        # 바이오메카닉 폴리곤, 5칸 컬러 게이지, 스티커 중심 스냅 & 웹캠 레코더 엔진
 ├── run_ui.bat                  # Windows 원클릭 가상환경 세팅 & 웹 UI 자동 실행기
 ├── requirements_portable.txt   # 핵심 의존성 패키지 목록
 └── outputs/
      └── sticker_placement_guide.png  # 7곳 원형 스티커 부착 가이드 도해 이미지 및 합성 결과물(.MP4/.PNG) 저장 폴더
```

---

<div align="center">

# 4th project 🖥️ TabMonitor Studio

</div>

**TabMonitor Studio**는 Windows PC와 Android 태블릿(Galaxy Tab 등)을 USB 케이블로 연결하여 **초저지연(60fps, NVIDIA NVENC 하드웨어 가속) 16:9 보조 모니터 및 터치스크린**으로 활용할 수 있게 해주는 오픈소스 프로젝트입니다.

---

## ✨ 주요 기능

- **🚀 초저지연 60fps 하드웨어 스트리밍**
  - Windows DXGI Desktop Duplication(`dxcam`) 기반 고속 화면 캡처와 NVIDIA `h264_nvenc` 하드웨어 인코더(Ultra-Low Latency, Zero-Latency, B-Frame 0)를 결합하여 지연 시간을 최소화했습니다.
  - Android 태블릿에서는 하드웨어 `MediaCodec`(`KEY_LOW_LATENCY`)을 통해 프레임을 즉시 디코딩 및 렌더링합니다.
- **📐 16:9 고정 화면비 & 자동 회전 지원**
  - 16:10 비율 태블릿에서도 왜곡 없는 정확한 **16:9 화면 비율**을 중앙 정렬로 유지하며, 태블릿을 회전할 때 가로 방향 자동 회전(`sensorLandscape`)에 대응합니다.
- **⚡ 무중단 실시간 해상도 전환 & 디스플레이 번호 고정**
  - 스트리밍 작동 중에도 GUI 버튼 클릭 한 번으로 **`1920 × 1080 (FHD 60p)`** ↔ **`1280 × 720 (HD 60p)`** 해상도를 즉시 전환할 수 있습니다.
  - 드라이버 재로드 없이 Win32 `ChangeDisplaySettingsExW` 인플레이스 전환을 사용하여 Windows 디스플레이 식별자(`\\.\DISPLAYx`)가 바뀌지 않고 고정됩니다.
- **👆 1:1 터치 ➔ 마우스 입력 & 실시간 커서 렌더링**
  - 태블릿 화면 터치 및 드래그가 보조 모니터 영역의 Windows 마우스 포인터 이동/클릭(`SendInput` & `SetCursorPos`)으로 1:1 매핑됩니다.
  - 관리자 권한으로 실행되는 프로그램 위에서도 터치 조작이 막히지 않도록 자동 권한 승격(UIPI 우회)을 지원하며, 보조 모니터 위의 마우스 커서를 실시간으로 화면에 렌더링합니다.
- **🔄 PC ↔ 태블릿 양방향 자동 실행 및 통일된 스튜디오 UI**
  - **PC에서 시작**: PC 프로그램에서 `▶ 보조모니터 시작`을 누르면 꺼져 있던 태블릿 화면이 자동으로 켜지고 앱이 실행되어 바로 연결됩니다.
  - **태블릿에서 시작**: 태블릿을 USB로 연결한 뒤 태블릿 앱에서 `▶ 보조모니터 시작`을 누르면 닫혀 있던 PC 프로그램이 자동으로 켜지며 스트리밍이 시작됩니다.
  - PC와 태블릿 앱 모두 일관된 다크 테마 대시보드 UI와 독립 팝업 로그 창(`📋 로그 창 열기`)을 제공합니다.

---

## 🛠️ 시스템 요구 사항

- **PC 운영체제**: Windows 10 / 11 (64-bit)
- **그래픽카드**: NVIDIA GPU (NVENC 하드웨어 인코딩 지원)
- **Python**: Python 3.10 이상
- **가상 디스플레이 드라이버**: [Virtual-Display-Driver (MttVDD)](https://github.com/itsmikethetech/Virtual-Display-Driver) (`1920x1080@60Hz`, `1280x720@60Hz` 모드 등록)
- **Android 기기**: Android 8.0 (API 26) 이상 태블릿 또는 스마트폰 (**USB 디버깅** 활성화 필수)

---

## 📂 프로젝트 구조

```text
TabMonitor/
├── app_gui.py          # Windows용 TabMonitor Studio 메인 GUI 및 영상/터치 스트리밍 서버
├── display_ctl.py      # Windows 가상 디스플레이 해상도 실시간 인플레이스 제어 모듈
├── usb_watcher.py      # 태블릿 앱 실행 감지 및 PC GUI 자동 실행 백그라운드 데몬
├── RUN.bat             # 콘솔 창 없이 PC GUI를 바로 실행하는 런처 스크립트
├── requirements.txt    # Python 필수 패키지 목록
└── android/            # Android용 TabMonitor Studio 앱 프로젝트 (Gradle)
    └── app/src/main/
        ├── AndroidManifest.xml
        └── java/com/tabmonitor/MainActivity.java
```

---

## 🚀 설치 및 실행 방법

### 1. Python 패키지 설치
```bash
pip install -r requirements.txt
```

### 2. ADB (Android Platform Tools) 준비
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)를 다운로드하여 프로젝트 폴더 내 `bin/adb.exe` 경로에 배치합니다.
- Android 태블릿의 **개발자 옵션 ➔ USB 디버깅**을 켜고 PC와 USB 케이블로 연결합니다.

### 3. 가상 디스플레이 드라이버 설정
- [Virtual Display Driver (MttVDD)](https://github.com/itsmikethetech/Virtual-Display-Driver)를 설치하고 `C:\VirtualDisplayDriver\vdd_settings.xml`에 `1920x1080@60Hz` 및 `1280x720@60Hz` 해상도를 등록합니다.
- Windows 디스플레이 설정(`Win + P`)에서 다중 디스플레이 모드를 **확장(Extend)**으로 설정합니다.

### 4. Android 앱 빌드 및 설치
`android/` 폴더에서 Gradle로 APK를 빌드하거나, 빌드된 `TabMonitor.apk`를 프로젝트 루트에 위치시킵니다. (첫 실행 시 PC 프로그램이 태블릿에 앱이 없으면 자동으로 `TabMonitor.apk`를 설치합니다.)
```bash
cd android
gradlew assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### 5. 프로그램 실행
`RUN.bat`을 더블클릭하거나 아래 명령어로 실행합니다:
```bash
pythonw app_gui.py
```

---

## 💡 사용 팁

- **실시간 해상도 변경**: 스트리밍 중에도 PC 또는 태블릿 대시보드에서 `1920 × 1080 (FHD 60p)` 또는 `1280 × 720 (HD 60p)` 버튼을 클릭하면 즉시 전환됩니다.
- **태블릿에서 대시보드 복귀**: 전체화면 스트리밍 도중 태블릿에서 **뒤로가기(Back)** 제스처를 하면 언제든 태블릿 내 TabMonitor Studio 대시보드 화면으로 돌아올 수 있습니다.
- **시스템 로그 확인**: 우측 상단의 `📋 로그 창 열기` 버튼을 누르면 별도의 팝업 창에서 연결 상태와 프레임/해상도 전환 로그를 실시간으로 확인할 수 있습니다.

---

<div align="center">

# 5th project 🎙️ OBS SRT Stream Live STT Subtitle Plugin

![Python 3.11](https://img.shields.io/badge/Python-3.8~3.11-blue?logo=python)
![OBS Studio](https://img.shields.io/badge/OBS_Studio-28+-302E31?logo=obsstudio)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

</div>

외부 프로그램이나 가상 오디오 케이블(Virtual Audio Cable)을 따로 실행할 필요 없이, **OBS Studio 내부에서만 100% 동작**하는 실시간 AI 자막(STT) 생성 스크립트 플러그인입니다.

OBS로 수신 중인 **SRT 스트림(미디어 소스, VLC 비디오 소스, 오디오 캡처 등)**의 오디오 신호를 LibOBS C-API 오디오 파이프라인에서 직접 가로채, 로컬 **Faster-Whisper AI (CPU `int8` 양자화)**로 실시간 자막을 생성하고 방송 화면의 **텍스트(GDI+) 소스**에 즉시 송출합니다.

---

## ✨ 주요 특징

- **OBS 내장 100% 단독 구동**: 별도의 외부 프로그램, 웹 브라우저, 콘솔 창을 띄울 필요가 없습니다.
- **SRT / 미디어 소스 오디오 직접 후킹**: 가상 오디오 케이블 없이 OBS 내부의 특정 오디오 소스만 직접 캡처합니다 (`obs_source_add_audio_capture_callback` 활용).
- **100% 무료 & 로컬 구동**: 유료 클라우드 API 키나 사용량 과금 없이 로컬 PC에서 무제한으로 동작합니다.
- **초저지연 CPU `int8` 가속**: 게임 플레이나 방송 인코딩(NVENC/x264)에 영향을 주지 않도록 가볍고 빠르게 동작합니다.
- **자동 재연결 및 타이밍 제어**: OBS 시작 시 소스가 늦게 로드되더라도 자동 재시도 타이머와 수동 재연결 버튼으로 안정적으로 연결합니다.
- **자막 자동 지움**: 음성이 멈추면 지정한 시간(기본 3.5초) 뒤에 화면의 자막을 자동으로 지워줍니다.

---

## 📂 파일 구조

```text
obs-srt-stt/
├── obs_srt_stt_plugin.py   # OBS Studio에 등록하는 메인 스크립트 플러그인
├── whisper_worker.py       # 백그라운드 음성 감지(VAD) 및 Faster-Whisper 추론 워커
├── requirements.txt        # Python 필수 패키지 목록
├── install.bat             # Windows용 원클릭 패키지 설치 스크립트
├── LICENSE                 # MIT 라이선스
└── README.md               # 프로젝트 문서
```

---

## 🛠️ 설치 및 설정 방법

### 1단계: Python 3.11 설치 (OBS Studio 호환 버전)
> **중요**: OBS Studio의 내장 Python 스크립팅 엔진은 **Python 3.8 ~ 3.11 (64-bit)** 버전만 공식 지원합니다. (Python 3.12 이상은 OBS에서 로드되지 않습니다.)

PowerShell 또는 명령 프롬프트(CMD)에서 아래 명령어로 Python 3.11을 설치할 수 있습니다:
```powershell
winget install Python.Python.3.11
```

### 2단계: 필수 라이브러리 설치
저장소를 다운로드(또는 `git clone`)한 뒤, 폴더 안의 **`install.bat`**을 더블클릭하거나 터미널에서 아래 명령어를 실행합니다:

```powershell
C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python311\python.exe -m pip install -r requirements.txt
```

### 3단계: OBS Studio에 Python 경로 연결 (최초 1회)
1. **OBS Studio** 실행
2. 상단 메뉴 **도구(Tools) → 스크립트(Scripts)** 클릭
3. **[Python 설정(Python Settings)]** 탭 클릭
4. **Python 설치 경로**를 Python 3.11 폴더로 지정합니다:
   ```text
   C:\Users\<사용자이름>\AppData\Local\Programs\Python\Python311
   ```
   *(상단에 `로드된 Python 버전: 3.11.x` 가 표시되면 정상입니다.)*

### 4단계: 스크립트 추가 및 소스 연결
1. **[스크립트(Scripts)]** 탭으로 이동 후 좌측 하단 **`+` (추가)** 버튼을 눌러 `obs_srt_stt_plugin.py`를 선택합니다.
2. 우측 설정 패널에서 다음 항목을 지정합니다:
   - **오디오 소스 (SRT 스트림 소스)**: 자막을 추출할 미디어 소스 / SRT 소스 선택
   - **자막 텍스트 소스 (화면 출력)**: 자막을 표시할 OBS `텍스트 (GDI+)` 소스 선택
   - **인식 언어**: `한국어 (ko)`, `English (en)`, `日本語 (ja)`, 또는 `자동 감지 (auto)`
   - **Whisper 모델**:
     - `tiny`: 초고속 반응 속도, CPU 점유율 최소
     - `base`: 속도와 정확도의 균형
     - `small`: 높은 인식 정확도 (한국어 방송 추천 ⭐)
3. **`✅ STT 자막 생성 활성화`** 체크박스를 켜면 즉시 실시간 자막 인식이 시작됩니다!

---

## 🔧 문제 해결 (Troubleshooting)

- **`OBS source '...' not found` 경고가 뜰 때**
  - OBS를 막 켰을 때는 씬의 미디어 소스가 초기화되기 전일 수 있습니다. 플러그인이 2초 간격으로 최대 10회 자동 재시도하며, 스크립트 설정 하단의 **`🔄 소스 재연결`** 버튼을 누르면 즉시 다시 연결됩니다.
- **최초 활성화 시 잠시 멈추는 현상**
  - 선택한 Whisper 모델(`tiny`, `base`, `small`)을 최초 1회 HuggingFace에서 다운로드하느라 수 초~수십 초가 소요될 수 있습니다. **`스크립트 로그(Script Log)`** 창에서 진행 상황을 확인할 수 있으며, 한 번 다운로드된 모델은 캐시되어 다음부터 즉시 로드됩니다.

---


<div align="center">

# 6th project 📂 Backup Explorer (개발진행중)

![Platform](https://img.shields.io/badge/PLATFORM-WINDOWS%2010%20%2F%2011-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Language](https://img.shields.io/badge/LANGUAGE-RUST-CE422B?style=for-the-badge&logo=rust&logoColor=white)
![GUI](https://img.shields.io/badge/GUI-EGUI%20%7C%20EFRAME-7F52FF?style=for-the-badge)
![Hashing](https://img.shields.io/badge/HASHING-BLAKE3-FF6F00?style=for-the-badge)
<br>
![License](https://img.shields.io/badge/LICENSE-MIT-68B723?style=for-the-badge)

</div>

<br>

**Backup Explorer**는 PC의 m.2 SSD 와 외장하드에 흩어져 있는 파일들을 **폴더 구조나 파일명에 상관없이 실제 내용(BLAKE3 해시) 기반으로 초고속 비교**하여, 미백업 파일 확인부터 중복 파일 안전 정리까지 한 번에 해결해 주는 Rust 기반 Windows 파일 탐색기 스타일 오픈소스 데스크톱 앱입니다.

---

## ✨ 주요 기능

- **🚀 3단계 초고속 해시 비교 & SSD/HDD 하이브리드 I/O**
  - **1단계(파일 크기 그룹화)** ➔ **2단계(앞/중간/뒤 48KB 고속 BLAKE3 샘플 해시)** ➔ **3단계(옵션: 100% 전체 바이트 정밀 해시)** 파이프라인으로 수백 GB의 사진·영상도 빠르게 대조합니다.
  - 내 PC(NVMe SSD)는 `rayon` 멀티스레드 병렬 읽기로, 기계식 외장하드(USB HDD)는 디스크 경로순 정렬 후 전용 단일 스레드 순차 읽기로 분리하여 **HDD 헤드 스래싱(병목)을 원천 차단**했습니다.
  - 실시간 진행률(`%`), 처리 속도, **남은 예상 시간(ETA)**을 표시하며, `Windows`, `Program Files`, `AppData`, `node_modules` 등 시스템·캐시 폴더 자동 제외를 지원합니다.
- **🧭 폴더 구조·파일명이 달라도 100% 탐지**
  - 디렉토리 깊이, 폴더 트리 구조, 파일 이름이 완전히 달라도 **파일 바이트 크기와 실제 데이터 해시값**만으로 동일 여부를 판별합니다.
  - **미백업 파일(PC에만 있음)**, **백업 완료 파일(양쪽 존재)**, **외장하드 내부 중복(낭비 용량 계산)**, **PC·외장 동일 비교**, **PC 내부 중복**, **외장하드 전용 파일**로 자동 분류합니다.
- **🖼️ Windows 파일 탐색기 스타일 미니멀 UI & 비동기 사진 미리보기**
  - **Noto Sans KR** 폰트 기반의 깔끔한 Windows 파일 탐색기 레이아웃과 가상 스크롤 하위 폴더 필터, 원클릭 유형 필터(`사진`, `영상`, `문서`, `오디오`, `압축`, `기타`) 및 사용자 지정 확장자·검색어 필터를 제공합니다.
  - 사진 파일 클릭 시 백그라운드 스레드에서 썸네일과 원본 해상도(`px`)를 비동기 로드하여 화면 멈춤 없이 눈으로 직접 비교할 수 있습니다.
  - Win32 Shell32 API(`SHOpenFolderAndSelectItems`)를 직접 호출하여 한글·공백·특수문자가 포함된 경로도 Windows 탐색기에서 해당 파일을 정확히 선택한 채로 바로 열어줍니다.
- **🧹 세밀한 스마트 중복 선택 & 원본 100% 보존 안전락**
  - `복사본명` (`(1)`, `복사본`, `copy`), `최신 사본`, `오래된 사본`, `깊은 폴더`, `상위 폴더`, `이 폴더만` 규칙으로 수백~수천 개의 중복 사본을 한 번에 체크할 수 있으며, **각 중복 그룹의 원본 최소 1개는 절대 삭제되지 않도록 자동 보호**합니다.
  - 정리 실행 전 **[정리 대상 경로 vs 보존 원본 경로]** 대조 확인 창을 띄운 뒤, 백그라운드 비동기 스레드로 안전하게 처리합니다:
    - **임시 격리 폴더로 이동**: 원래 드라이브와 폴더 구조를 그대로 유지한 채 `_중복격리보관함`으로 이동 (추후 확인 후 일괄 삭제 또는 복원 가능)
    - **Windows 휴지통으로 이동**: Win32 `SHFileOperationW` 배치 호출로 휴지통에 안전하게 이동
- **📥 엑셀 호환 CSV 리포트 내보내기**
  - 현재 필터링된 분석 결과와 동일 파일의 분산 저장 경로 목록을 UTF-8 BOM CSV 파일로 내보낼 수 있습니다.

---

## 🛠️ 시스템 요구 사항

- **운영체제**: Windows 10 / 11 (64-bit)
- **빌드 도구** (소스에서 직접 빌드 시): [Rust Toolchain](https://www.rust-lang.org/tools/install) (MSVC 타겟, Rust 2021 Edition)
- **단독 실행**: 빌드된 단일 실행 파일(`BackupExplorer.exe`)은 별도의 런타임이나 라이브러리 설치 없이 바로 실행됩니다.

---

## 📂 프로젝트 구조

```text
backup-explorer/
├── Cargo.toml                  # 패키지 메타데이터 및 Rust 크레이트 의존성 설정
├── Cargo.lock                  # 의존성 버전 잠금 파일
├── LICENSE                     # MIT 라이선스
├── README.md                   # 프로젝트 설명서
├── scripts/
│   └── install_windows.ps1     # Windows 로컬 설치 및 바탕화면/시작 메뉴 바로가기 생성 스크립트
└── src/
    ├── main.rs                 # Windows 탐색기 스타일 GUI, 가상 스크롤, 비동기 썸네일 로더
    ├── scanner.rs              # 3단계 BLAKE3 비교 엔진, SSD/HDD 하이브리드 I/O, ETA 계산기
    └── cleanup.rs              # 스마트 자동 선택 규칙, 원본 안전락, 격리 폴더/휴지통 정리 모듈
```

---

## 🚀 설치 및 실행 방법

### 1. 저장소 클론 및 릴리즈 빌드
```powershell
git clone https://github.com/<your-username>/backup-explorer.git
cd backup-explorer
cargo build --release
```
빌드가 완료되면 `target\release\backup-explorer.exe` 단일 실행 파일이 생성됩니다.

### 2. Windows 설치 (바로가기 자동 등록)
아래 PowerShell 스크립트를 실행하면 `%LOCALAPPDATA%\Programs\BackupExplorer\BackupExplorer.exe`에 설치되고 **바탕화면**과 **시작 메뉴**에 `백업 & 중복 파일 탐색기` 바로가기가 자동으로 생성됩니다.
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

### 3. 프로그램 실행
바탕화면의 `백업 & 중복 파일 탐색기` 아이콘을 더블클릭하거나 `target\release\backup-explorer.exe`를 직접 실행합니다.

---

## 💡 사용 팁

- **빠른 폴더 등록**: 상단 `내 PC` 옆의 `자동` 버튼을 누르면 `사진`, `동영상`, `문서`, `바탕화면`, `다운로드` 폴더가 한 번에 추가되며, `외장하드` 옆의 `감지` 버튼을 누르면 연결된 외장 드라이브가 자동으로 추가됩니다.
- **초고속 vs 100% 전체 해시 전환**: 우측 상단 `옵션` 메뉴에서 `초고속 샘플 해시 모드`를 켜면 대용량 영상도 수 초~수 분 만에 비교하며, 해제하면 파일의 첫 바이트부터 끝 바이트까지 100% 전체를 읽어 대조합니다.
- **탐색기 바로 열기**: 중앙 파일 목록이나 중복 그룹에서 항목을 **더블클릭**하거나, 우측 상세 패널의 `위치` 버튼을 누르면 해당 파일이 선택된 상태로 Windows 탐색기가 즉시 열립니다.
- **호버 툴팁 활용**: 모든 버튼과 좌측 분류 메뉴, 스마트 선택 옵션에 마우스 커서를 올리면 상세 기능과 용량 요약이 툴팁으로 표시됩니다.

---

## 📄 라이선스

All project is licensed under the MIT License.

