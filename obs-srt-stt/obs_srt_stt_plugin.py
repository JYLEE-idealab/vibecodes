"""
OBS Studio Native Plugin Script: SRT Stream Live STT Subtitle Generator (100% Free & Local).
Runs entirely INSIDE OBS Studio via 'Tools -> Scripts' without any external applications.

Captures raw audio from any designated incoming stream (SRT Media Source, VLC, etc.),
transcribes it in real-time using local Whisper AI (CPU int8), and automatically
displays the subtitles onto the broadcast screen via an OBS Text (GDI+) source.
"""

import sys
import os

script_dir = os.path.dirname(os.path.abspath(__file__))

# 1. Clean sys.path: Purge any conflicting .venv or incompatible Python 3.12+ paths
sys.path = [p for p in sys.path if ".venv" not in p.lower() and "python313" not in p.lower() and "python312" not in p.lower()]

# 2. Dynamically detect user's Python 3.11 site-packages and ensure it's on sys.path
py311_candidates = [
    os.path.join(sys.exec_prefix, "Lib", "site-packages"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\Lib\site-packages"),
]
for candidate in py311_candidates:
    if os.path.isdir(candidate) and candidate not in sys.path:
        sys.path.insert(0, candidate)

if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

import ctypes
import logging
import numpy as np

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OBSSRTSTTPlugin")

try:
    import obspython as obs
except ImportError:
    obs = None
    logger.warning("obspython not detected. Running in test/standalone mode.")

import importlib
if "whisper_worker" in sys.modules:
    importlib.reload(sys.modules["whisper_worker"])
from whisper_worker import WhisperWorker

# LibOBS C-ABI Structures
class AudioData(ctypes.Structure):
    _fields_ = [
        ("data", ctypes.c_void_p * 8),  # 8 AV planes (float* planar)
        ("frames", ctypes.c_uint32),
        ("timestamp", ctypes.c_uint64),
    ]

OBS_AUDIO_CALLBACK = ctypes.CFUNCTYPE(
    None,
    ctypes.c_void_p,               # param
    ctypes.c_void_p,               # obs_source_t*
    ctypes.POINTER(AudioData),     # const struct audio_data*
    ctypes.c_bool                  # muted
)

class PluginState:
    def __init__(self):
        self.srt_source_name = ""
        self.text_source_name = ""
        self.language = "ko"
        self.model_size = "tiny"
        self.clear_timeout = 3.5
        self.max_chars = 28
        self.is_enabled = False

        self.worker: WhisperWorker = None
        self.hooked_source = None
        self.audio_callback_c = None
        self.obs_dll = None

        self._load_libobs()

    def _load_libobs(self):
        obs_dll_path = r"C:\Program Files\obs-studio\bin\64bit\obs.dll"
        if os.path.isfile(obs_dll_path):
            try:
                bin_dir = os.path.dirname(obs_dll_path)
                if hasattr(os, "add_dll_directory"):
                    os.add_dll_directory(bin_dir)
                self.obs_dll = ctypes.CDLL(obs_dll_path)

                self.obs_dll.obs_get_source_by_name.argtypes = [ctypes.c_char_p]
                self.obs_dll.obs_get_source_by_name.restype = ctypes.c_void_p

                self.obs_dll.obs_source_release.argtypes = [ctypes.c_void_p]
                self.obs_dll.obs_source_release.restype = None

                self.obs_dll.obs_source_add_audio_capture_callback.argtypes = [
                    ctypes.c_void_p, OBS_AUDIO_CALLBACK, ctypes.c_void_p
                ]
                self.obs_dll.obs_source_remove_audio_capture_callback.argtypes = [
                    ctypes.c_void_p, OBS_AUDIO_CALLBACK, ctypes.c_void_p
                ]
            except Exception as e:
                logger.error(f"Failed to load libobs: {e}")

state = PluginState()

def _resample_to_16k_mono(mono_float32: np.ndarray, source_rate: int = 48000) -> np.ndarray:
    """Fast linear interpolation to 16kHz."""
    if len(mono_float32) == 0:
        return mono_float32
    if source_rate == 16000:
        return mono_float32

    target_len = int(round(len(mono_float32) * 16000 / source_rate))
    if target_len <= 0:
        return np.array([], dtype=np.float32)

    indices = np.linspace(0, len(mono_float32) - 1, target_len)
    return np.interp(indices, np.arange(len(mono_float32)), mono_float32).astype(np.float32)

def _c_audio_callback(param, source_ptr, audio_data_ptr, muted):
    """Raw PCM audio callback triggered directly from OBS's audio render pipeline."""
    if muted or not state.is_enabled or not state.worker or not audio_data_ptr:
        return

    try:
        adata = audio_data_ptr.contents
        frames = adata.frames
        if frames == 0 or not adata.data[0]:
            return

        # Read float32 audio samples (4 bytes per sample)
        byte_len = frames * 4
        raw0 = ctypes.string_at(adata.data[0], byte_len)
        arr0 = np.frombuffer(raw0, dtype=np.float32)

        # Average channels if stereo
        if adata.data[1]:
            raw1 = ctypes.string_at(adata.data[1], byte_len)
            arr1 = np.frombuffer(raw1, dtype=np.float32)
            mono = (arr0 + arr1) * 0.5
        else:
            mono = arr0

        # Downsample 48kHz (OBS internal standard) to 16kHz
        pcm_16k = _resample_to_16k_mono(mono, 48000)
        state.worker.add_audio(pcm_16k)
    except Exception:
        pass

def on_transcript_received(text: str):
    """Update OBS Text Source with the generated subtitle."""
    if not obs or not state.text_source_name:
        return

    source = obs.obs_get_source_by_name(state.text_source_name)
    if source:
        settings = obs.obs_data_create()
        obs.obs_data_set_string(settings, "text", text)
        obs.obs_source_update(source, settings)
        obs.obs_data_release(settings)
        obs.obs_source_release(source)

# Retry counter for delayed source hook
_hook_retry_count = 0
_MAX_HOOK_RETRIES = 10   # 10 × 2s = 최대 20초 대기

def _try_hook_source():
    """OBS 타이머 콜백: 소스가 로드될 때까지 2초마다 재시도."""
    global _hook_retry_count
    obs.timer_remove(_try_hook_source)  # 타이머 해제 (one-shot 방식)

    if not state.is_enabled or not state.worker:
        _hook_retry_count = 0
        return  # STT가 비활성화됐거나 worker가 없으면 중단

    if state.hooked_source:
        _hook_retry_count = 0
        return  # 이미 연결됨

    source_ptr = state.obs_dll.obs_get_source_by_name(state.srt_source_name.encode("utf-8"))
    if source_ptr:
        state.hooked_source = source_ptr
        state.audio_callback_c = OBS_AUDIO_CALLBACK(_c_audio_callback)
        state.obs_dll.obs_source_add_audio_capture_callback(
            source_ptr, state.audio_callback_c, None
        )
        _hook_retry_count = 0
        logger.info(f"✅ Audio capture successfully hooked to '{state.srt_source_name}'!")
    else:
        _hook_retry_count += 1
        if _hook_retry_count < _MAX_HOOK_RETRIES:
            logger.warning(f"⏳ OBS source '{state.srt_source_name}' not ready yet — retry {_hook_retry_count}/{_MAX_HOOK_RETRIES} in 2s...")
            obs.timer_add(_try_hook_source, 2000)
        else:
            _hook_retry_count = 0
            logger.error(f"❌ OBS source '{state.srt_source_name}' not found after {_MAX_HOOK_RETRIES} retries. 소스 이름을 확인하거나 '🔄 소스 재연결' 버튼을 누르세요.")

def start_stt():
    global _hook_retry_count
    if not state.srt_source_name:
        logger.warning("No SRT audio source selected.")
        return

    logger.info(f"Starting STT on source: {state.srt_source_name}")

    # 1. Initialize Whisper Worker
    try:
        state.worker = WhisperWorker(
            model_size=state.model_size,
            language=state.language,
            on_transcript=on_transcript_received,
            auto_clear_seconds=state.clear_timeout,
            max_chars_per_line=state.max_chars
        )
        state.worker.start()
    except Exception as e:
        logger.error(f"Failed to start Whisper worker: {e}")
        return

    # 2. Hook into the OBS source with retry (handles startup race condition)
    if state.obs_dll and state.srt_source_name:
        source_ptr = state.obs_dll.obs_get_source_by_name(state.srt_source_name.encode("utf-8"))
        if source_ptr:
            state.hooked_source = source_ptr
            state.audio_callback_c = OBS_AUDIO_CALLBACK(_c_audio_callback)
            state.obs_dll.obs_source_add_audio_capture_callback(
                source_ptr, state.audio_callback_c, None
            )
            logger.info(f"✅ Audio capture successfully hooked to '{state.srt_source_name}'!")
        else:
            # Source not ready yet (OBS startup race) — start retry timer
            _hook_retry_count = 0
            logger.warning(f"⏳ OBS source '{state.srt_source_name}' not found — will retry in 2s (OBS 초기화 대기 중)...")
            obs.timer_add(_try_hook_source, 2000)

def stop_stt():
    global _hook_retry_count
    logger.info("Stopping STT...")

    # Cancel any pending retry timer
    if obs:
        obs.timer_remove(_try_hook_source)
    _hook_retry_count = 0

    # Detach audio capture callback
    if state.hooked_source and state.audio_callback_c and state.obs_dll:
        try:
            state.obs_dll.obs_source_remove_audio_capture_callback(
                state.hooked_source, state.audio_callback_c, None
            )
            state.obs_dll.obs_source_release(state.hooked_source)
        except Exception as e:
            logger.error(f"Error unhooking audio capture: {e}")

    state.hooked_source = None
    state.audio_callback_c = None

    # Stop worker
    if state.worker:
        state.worker.stop()
        state.worker = None

    # Clear subtitle text
    on_transcript_received("")
    logger.info("STT stopped.")

def btn_reconnect_clicked(props, prop):
    """수동 재연결 버튼: 소스를 다시 찾아 훅 연결."""
    if not state.is_enabled:
        logger.warning("STT가 비활성화 상태입니다. 먼저 체크박스를 활성화하세요.")
        return True
    if not state.srt_source_name:
        logger.warning("오디오 소스가 선택되지 않았습니다.")
        return True

    # Remove old hook if any
    if state.hooked_source and state.audio_callback_c and state.obs_dll:
        try:
            state.obs_dll.obs_source_remove_audio_capture_callback(
                state.hooked_source, state.audio_callback_c, None
            )
            state.obs_dll.obs_source_release(state.hooked_source)
        except Exception:
            pass
        state.hooked_source = None
        state.audio_callback_c = None

    # Cancel pending retry timer
    obs.timer_remove(_try_hook_source)

    # Try immediate reconnect
    source_ptr = state.obs_dll.obs_get_source_by_name(state.srt_source_name.encode("utf-8"))
    if source_ptr:
        state.hooked_source = source_ptr
        state.audio_callback_c = OBS_AUDIO_CALLBACK(_c_audio_callback)
        state.obs_dll.obs_source_add_audio_capture_callback(
            source_ptr, state.audio_callback_c, None
        )
        logger.info(f"✅ 수동 재연결 성공: '{state.srt_source_name}'")
    else:
        logger.error(f"❌ 재연결 실패: OBS source '{state.srt_source_name}' not found.")
    return True

# ----------------- OBS Studio Script Callbacks -----------------

def script_description():
    return """<h2>SRT 스트림 실시간 STT 자막 플러그인 (100% 무료 / OBS 내장)</h2>
<p>OBS로 들어오는 <b>SRT 스트림(미디어 소스 / VLC 비디오 소스)</b>의 오디오를 OBS 내부에서 직접 가로채 실시간으로 자막을 생성합니다.</p>
<ul>
    <li><b>외부 프로그램 실행 불필요</b>: OBS Studio 내부에서 완벽 구동</li>
    <li><b>100% 무료 무제한</b>: 유료 API 키, 결제 없이 로컬 고성능 Whisper AI 구동</li>
    <li><b>초저지연 CPU int8 가속</b>: 방송 인코딩에 방해 없이 가볍고 빠르게 동작</li>
</ul>"""

def script_properties():
    if not obs:
        return None

    props = obs.obs_properties_create()

    # 1. SRT Audio Source Selector
    p_src = obs.obs_properties_add_list(
        props, "srt_source", "오디오 소스 (SRT 스트림 소스)",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_property_list_add_string(p_src, "(선택 안 함)", "")

    sources = obs.obs_enum_sources()
    if sources:
        for s in sources:
            flags = obs.obs_source_get_output_flags(s)
            # Include all sources that can output audio (Media source, VLC, audio capture, etc.)
            if flags & obs.OBS_SOURCE_AUDIO:
                name = obs.obs_source_get_name(s)
                obs.obs_property_list_add_string(p_src, name, name)
        obs.source_list_release(sources)

    # 2. Target Text Source Selector
    p_txt = obs.obs_properties_add_list(
        props, "text_source", "자막 텍스트 소스 (화면 출력)",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_property_list_add_string(p_txt, "(선택 안 함)", "")

    sources = obs.obs_enum_sources()
    if sources:
        for s in sources:
            source_id = obs.obs_source_get_id(s)
            if "text" in source_id.lower():
                name = obs.obs_source_get_name(s)
                obs.obs_property_list_add_string(p_txt, name, name)
        obs.source_list_release(sources)

    # 3. Language Selector
    p_lang = obs.obs_properties_add_list(
        props, "language", "인식 언어",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_property_list_add_string(p_lang, "한국어 (ko)", "ko")
    obs.obs_property_list_add_string(p_lang, "English (en)", "en")
    obs.obs_property_list_add_string(p_lang, "日本語 (ja)", "ja")
    obs.obs_property_list_add_string(p_lang, "자동 감지 (auto)", "auto")

    # 4. Model Size
    p_model = obs.obs_properties_add_list(
        props, "model_size", "Whisper 모델 (속도/품질)",
        obs.OBS_COMBO_TYPE_LIST, obs.OBS_COMBO_FORMAT_STRING
    )
    obs.obs_property_list_add_string(p_model, "tiny (초고속, 실시간 방송 권장 ⭐)", "tiny")
    obs.obs_property_list_add_string(p_model, "base (표준)", "base")
    obs.obs_property_list_add_string(p_model, "small (고품질)", "small")

    # 5. Clear Timeout
    obs.obs_properties_add_float_slider(
        props, "clear_timeout", "자막 유지 시간(초)", 1.0, 10.0, 0.5
    )

    # 6. Max Characters per line
    obs.obs_properties_add_int(
        props, "max_chars", "줄당 최대 글자 수", 15, 60, 1
    )

    # 7. Enable Checkbox
    obs.obs_properties_add_bool(props, "is_enabled", "✅ STT 자막 생성 활성화")

    # 8. Manual reconnect button (for when OBS startup race causes source-not-found)
    obs.obs_properties_add_button(
        props, "btn_reconnect", "🔄 소스 재연결 (소스를 못 찾을 때 클릭)", btn_reconnect_clicked
    )

    return props

def script_update(settings):
    if not obs:
        return

    prev_enabled = state.is_enabled
    state.srt_source_name = obs.obs_data_get_string(settings, "srt_source")
    state.text_source_name = obs.obs_data_get_string(settings, "text_source")
    state.language = obs.obs_data_get_string(settings, "language") or "ko"
    state.model_size = obs.obs_data_get_string(settings, "model_size") or "tiny"
    state.clear_timeout = obs.obs_data_get_double(settings, "clear_timeout") or 3.5
    state.max_chars = obs.obs_data_get_int(settings, "max_chars") or 28
    state.is_enabled = obs.obs_data_get_bool(settings, "is_enabled")

    # Handle enable/disable toggle
    if state.is_enabled and not prev_enabled:
        start_stt()
    elif not state.is_enabled and prev_enabled:
        stop_stt()

def script_load(settings):
    logger.info("OBS SRT STT script loaded.")
    script_update(settings)

def script_unload():
    logger.info("OBS SRT STT script unloading...")
    stop_stt()
