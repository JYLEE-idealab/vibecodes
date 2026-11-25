package com.example.securelivestreamingapp

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioManager
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.Locale
import java.util.UUID
import java.util.concurrent.LinkedBlockingQueue

class TtsManager(private val context: Context) {
    private var tts: TextToSpeech? = null
    var isReady = false
        private set
    private val pendingQueue = LinkedBlockingQueue<String>(50)

    interface TtsListener {
        fun onReady()
        fun onError(msg: String)
    }

    var listener: TtsListener? = null

    fun init() {
        tts = TextToSpeech(context) { status ->
            if (status == TextToSpeech.SUCCESS) {
                tts?.language = Locale.KOREAN
                tts?.setSpeechRate(1.0f)
                tts?.setPitch(1.0f)

                // USAGE_MEDIA -> 외장 라우드스피커 / 블루투스 A2DP 고음질 출력
                val audioAttributes = AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_MEDIA)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                    .build()
                tts?.setAudioAttributes(audioAttributes)

                tts?.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String?) {
                        Log.d("TtsManager", "음성 출력 시작: $utteranceId")
                    }
                    override fun onDone(utteranceId: String?) {
                        Log.d("TtsManager", "음성 출력 완료: $utteranceId")
                    }
                    override fun onError(utteranceId: String?) {
                        Log.e("TtsManager", "음성 출력 에러: $utteranceId")
                    }
                })
                isReady = true
                Log.d("TtsManager", "TTS 초기화 완료 (STREAM_MUSIC / USAGE_MEDIA)")
                while (pendingQueue.isNotEmpty()) {
                    speak(pendingQueue.poll() ?: break)
                }
                listener?.onReady()
            } else {
                Log.e("TtsManager", "TTS 초기화 실패: $status")
                listener?.onError("TTS 초기화 실패 (status=$status)")
            }
        }
    }

    fun speak(text: String) {
        if (!isReady) {
            Log.w("TtsManager", "TTS 준비 전, 대기열 저장: $text")
            if (pendingQueue.size < 50) pendingQueue.offer(text)
            return
        }
        val utteranceId = UUID.randomUUID().toString()
        val params = Bundle().apply {
            putInt(TextToSpeech.Engine.KEY_PARAM_STREAM, AudioManager.STREAM_MUSIC)
            putFloat(TextToSpeech.Engine.KEY_PARAM_VOLUME, 1.0f)
        }
        Log.d("TtsManager", "TTS 읽기 요청: $text")
        tts?.speak(text, TextToSpeech.QUEUE_ADD, params, utteranceId)
    }

    fun stop() {
        tts?.stop()
        pendingQueue.clear()
    }

    fun release() {
        stop()
        tts?.shutdown()
        tts = null
        isReady = false
    }
}
