package com.example.securelivestreamingapp

import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFocusRequest
import android.media.AudioManager
import android.os.Build
import android.util.Log

enum class AudioMode {
    BLUETOOTH,    // 블루투스 이어폰
    PHONE,        // 폰 내장
    UVC_WIRED     // UVC 캠 유선
}

class AudioRoutingManager(private val context: Context) {
    private val audioManager = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    private var focusRequest: AudioFocusRequest? = null
    var currentMode: AudioMode = AudioMode.PHONE
        private set

    fun applyMode(mode: AudioMode) {
        currentMode = mode
        when (mode) {
            AudioMode.BLUETOOTH -> {
                try {
                    audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
                    audioManager.startBluetoothSco()
                    audioManager.isBluetoothScoOn = true
                    audioManager.isSpeakerphoneOn = false
                    Log.d("AudioRouting", "블루투스 모드 적용 (SCO 활성화)")
                } catch (e: Exception) {
                    Log.e("AudioRouting", "블루투스 SCO 시작 실패: ${e.message}")
                }
            }
            AudioMode.PHONE -> {
                audioManager.stopBluetoothSco()
                audioManager.isBluetoothScoOn = false
                audioManager.mode = AudioManager.MODE_NORMAL
                audioManager.isSpeakerphoneOn = false  // 녹음/스트리밍 중 스피커폰은 에코 유발 → 이어폰 또는 기기 내장 마이크 자연 라우팅
                Log.d("AudioRouting", "폰 내장 모드 적용 (에코 방지: 스피커폰 OFF)")
            }
            AudioMode.UVC_WIRED -> {
                audioManager.stopBluetoothSco()
                audioManager.isBluetoothScoOn = false
                audioManager.mode = AudioManager.MODE_IN_COMMUNICATION
                audioManager.isSpeakerphoneOn = false
                Log.d("AudioRouting", "UVC 유선 모드 적용")
            }
        }
        requestAudioFocus()
    }

    private fun requestAudioFocus() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val attrs = AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH)
                .build()
            focusRequest = AudioFocusRequest.Builder(AudioManager.AUDIOFOCUS_GAIN)
                .setAudioAttributes(attrs)
                .setAcceptsDelayedFocusGain(false)
                .setOnAudioFocusChangeListener {}
                .build()
            audioManager.requestAudioFocus(focusRequest!!)
        }
    }

    fun release() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            focusRequest?.let { audioManager.abandonAudioFocusRequest(it) }
        }
        audioManager.stopBluetoothSco()
        audioManager.isBluetoothScoOn = false
        audioManager.mode = AudioManager.MODE_NORMAL
    }
}
