# 🎙️ OBS SRT Stream Live STT Subtitle Plugin

![Python 3.11](https://img.shields.io/badge/Python-3.8~3.11-blue?logo=python)
![OBS Studio](https://img.shields.io/badge/OBS_Studio-28+-302E31?logo=obsstudio)
![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)

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
> [!IMPORTANT]
> OBS Studio의 내장 Python 스크립팅 엔진은 **Python 3.8 ~ 3.11 (64-bit)** 버전만 공식 지원합니다. (Python 3.12 이상은 OBS에서 로드되지 않습니다.)

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

## 📜 라이선스

이 프로젝트는 [MIT License](./LICENSE)에 따라 배포됩니다.
