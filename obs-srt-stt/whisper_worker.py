"""
Lightweight real-time Whisper STT Worker for OBS Studio.
Runs locally on CPU using int8 quantization for ultra-fast, zero-cost transcription
without lagging OBS stream encoding.
"""

import time
import queue
import threading
import logging
from typing import Callable, Optional
import numpy as np
from faster_whisper import WhisperModel

logger = logging.getLogger("WhisperWorker")

class WhisperWorker:
    def __init__(
        self,
        model_size: str = "tiny",
        language: str = "ko",
        on_transcript: Optional[Callable[[str], None]] = None,
        auto_clear_seconds: float = 3.5,
        silence_threshold: float = 0.005,
        max_chars_per_line: int = 28,
    ):
        self.model_size = model_size
        self.language = language if language != "auto" else None
        self.on_transcript = on_transcript
        self.auto_clear_seconds = auto_clear_seconds
        self.silence_threshold = silence_threshold
        self.max_chars_per_line = max_chars_per_line

        self.audio_queue = queue.Queue()
        self.is_running = False
        self.worker_thread: Optional[threading.Thread] = None

        # State tracking
        self.last_speech_time = 0.0
        self.clear_timer_active = False

        # Load model with CPU int8 quantization for high performance
        logger.info(f"Loading faster-whisper model '{model_size}' (CPU int8)...")
        self.model = WhisperModel(model_size, device="cpu", compute_type="int8")
        logger.info(f"Whisper model '{model_size}' successfully loaded!")

    def add_audio(self, audio_data: np.ndarray):
        """Feed 16kHz 1D float32 audio chunk from OBS callback."""
        if self.is_running and len(audio_data) > 0:
            self.audio_queue.put(audio_data)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.worker_thread = threading.Thread(target=self._process_loop, daemon=True)
        self.worker_thread.start()
        logger.info("Whisper STT Worker started.")

    def stop(self):
        self.is_running = False
        self.audio_queue.put(None)
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=2.0)
        logger.info("Whisper STT Worker stopped.")

    def _format_subtitle(self, text: str) -> str:
        text = text.strip()
        if not text or len(text) <= self.max_chars_per_line:
            return text

        words = text.split(" ")
        lines = []
        cur_line = []
        cur_len = 0
        for w in words:
            if cur_len + len(w) + 1 > self.max_chars_per_line and cur_line:
                lines.append(" ".join(cur_line))
                cur_line = [w]
                cur_len = len(w)
            else:
                cur_line.append(w)
                cur_len += len(w) + 1

        if cur_line:
            lines.append(" ".join(cur_line))

        if len(lines) > 2:
            lines = lines[-2:]
        return "\n".join(lines)

    def _process_loop(self):
        # 16kHz audio: 16000 samples = 1 second
        sample_rate = 16000
        speech_buffer = []
        silence_counter = 0
        is_speaking = False

        while self.is_running:
            # Check for subtitle auto-clear timeout
            if self.clear_timer_active and self.auto_clear_seconds > 0:
                if time.time() - self.last_speech_time >= self.auto_clear_seconds:
                    self.clear_timer_active = False
                    if self.on_transcript:
                        self.on_transcript("")

            try:
                chunk = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if chunk is None or not self.is_running:
                break

            # Calculate RMS energy of current audio frame
            rms = np.sqrt(np.mean(chunk**2)) if len(chunk) > 0 else 0

            if rms > self.silence_threshold:
                # Speech detected
                is_speaking = True
                silence_counter = 0
                speech_buffer.append(chunk)
            else:
                if is_speaking:
                    silence_counter += 1
                    speech_buffer.append(chunk)

            # Calculate accumulated buffer length in seconds
            buffer_len_samples = sum(len(c) for c in speech_buffer)
            buffer_seconds = buffer_len_samples / sample_rate

            # Check if we should transcribe:
            # 1) Must have accumulated at least 0.8s of speech before triggering on pause
            # 2) Trigger when speech pauses for ~0.4s (approx 20 chunks)
            # 3) Or trigger when continuous speech reaches 3.5s
            should_transcribe = False
            if is_speaking:
                if silence_counter >= 20 and buffer_seconds >= 0.8:
                    should_transcribe = True
                elif buffer_seconds >= 3.5:
                    should_transcribe = True
                elif silence_counter >= 40 and buffer_seconds < 0.8:
                    # Discard brief background click/blip (<0.8s) after prolonged silence
                    speech_buffer = []
                    silence_counter = 0
                    is_speaking = False

            if should_transcribe and speech_buffer:
                audio_array = np.concatenate(speech_buffer)
                speech_buffer = []
                silence_counter = 0
                is_speaking = False

                # Transcribe speech segment
                try:
                    segments, _ = self.model.transcribe(
                        audio_array,
                        language=self.language,
                        beam_size=1,            # Greedy decoding for fastest latency
                        temperature=0.0,
                        vad_filter=False,       # Energy buffer already segments speech cleanly
                    )

                    text_parts = [seg.text.strip() for seg in segments if seg.text.strip()]
                    full_text = " ".join(text_parts).strip()

                    if full_text:
                        logger.info(f"✨ 실시간 자막 인식: {full_text}")
                        formatted = self._format_subtitle(full_text)
                        self.last_speech_time = time.time()
                        self.clear_timer_active = True
                        if self.on_transcript:
                            self.on_transcript(formatted)
                except Exception as e:
                    logger.error(f"Whisper transcribe error: {e}")
