package com.example.securelivestreamingapp

import android.content.Context
import android.util.Log
import com.pedro.common.ConnectChecker
import com.pedro.library.srt.SrtCamera1
import com.pedro.library.view.OpenGlView

class StreamingManager(
    private val context: Context,
    private val openGlView: OpenGlView,
    private val listener: StreamingListener
) : ConnectChecker {

    interface StreamingListener {
        fun onConnectionSuccess()
        fun onConnectionFailed(reason: String)
        fun onDisconnect()
    }

    private var srtCamera: SrtCamera1? = null
    var isUvcMode = false
        private set
    private var needRePrepare = false

    fun initMode(useUvc: Boolean) {
        this.isUvcMode = useUvc
        if (srtCamera == null) {
            srtCamera = SrtCamera1(openGlView, this)
        }
    }

    private fun prepareEncoders(): Boolean {
        // 스마트폰 및 다양한 기기의 카메라 하드웨어에 맞춰 1080p -> 720p -> 480p 순차 자동 폴백
        val resolutions = listOf(
            Pair(1920, 1080),
            Pair(1280, 720),
            Pair(854, 480),
            Pair(640, 480)
        )
        for ((w, h) in resolutions) {
            val bitrate = if (w >= 1920) 5 * 1024 * 1024 else if (w >= 1280) 3 * 1024 * 1024 else 1024 * 1024
            val videoOk = srtCamera?.prepareVideo(w, h, 30, bitrate, 2) == true
            val audioOk = srtCamera?.prepareAudio(128 * 1024, 44100, true) == true
            if (videoOk && audioOk) {
                Log.d("StreamingManager", "인코더 초기화 성공: ${w}x${h}, bitrate=${bitrate}")
                return true
            } else {
                Log.w("StreamingManager", "${w}x${h} 인코더 초기화 실패, 다음 해상도 시도")
            }
        }
        return false
    }

    fun startPreview() {
        if (srtCamera == null) {
            srtCamera = SrtCamera1(openGlView, this)
        }
        if (srtCamera?.isOnPreview == true) return

        if (prepareEncoders()) {
            srtCamera?.startPreview()
            needRePrepare = false
            Log.d("StreamingManager", "카메라 프리뷰 시작 완료 (Camera1)")
        } else {
            Log.e("StreamingManager", "모든 해상도 프리뷰 인코더 prepare 실패")
        }
    }

    fun startStream(url: String, streamId: String, passphrase: String) {
        if (srtCamera?.isStreaming == true) return

        val baseUrl = url.trimEnd('/')
        val finalUrl = if (baseUrl.matches(Regex("srt://[^/]+:\\d+"))) {
            val streamName = if (streamId.isNotEmpty()) streamId else "live"
            "$baseUrl/$streamName"
        } else {
            baseUrl
        }

        try {
            if (srtCamera == null) {
                srtCamera = SrtCamera1(openGlView, this)
            }

            // 이전 송출 종료 등으로 인코더가 stop되었거나 프리뷰가 꺼져 있으면 재초기화
            if (needRePrepare || srtCamera?.isOnPreview != true) {
                if (srtCamera?.isOnPreview == true) {
                    srtCamera?.stopPreview()
                }
                if (!prepareEncoders()) {
                    listener.onConnectionFailed("인코더 초기화 실패")
                    return
                }
                srtCamera?.startPreview()
                needRePrepare = false
            }

            // passphrase 설정 (10자 이상일 때만 암호화 활성화)
            if (passphrase.length >= 10) {
                getSrtStreamClient()?.setPassphrase(
                    passphrase,
                    com.pedro.srt.srt.packets.control.handshake.EncryptionType.AES128
                )
            }

            Log.d("StreamingManager", "SRT 송출 시작 호출: $finalUrl")
            srtCamera?.startStream(finalUrl)

        } catch (e: Exception) {
            Log.e("StreamingManager", "startStream 오류", e)
            listener.onConnectionFailed("초기화 오류: ${e.message}")
        }
    }

    fun stopStream() {
        try {
            if (srtCamera?.isStreaming == true) {
                srtCamera?.stopStream()
                needRePrepare = true
                Log.d("StreamingManager", "SRT 송출 중단 완료")
            }
        } catch (e: Exception) {
            Log.e("StreamingManager", "stopStream 오류", e)
        }
    }

    fun isStreaming(): Boolean = srtCamera?.isStreaming == true

    fun switchCamera() {
        try {
            srtCamera?.switchCamera()
            Log.d("StreamingManager", "카메라 전/후면 전환 완료 (isFront: ${srtCamera?.isFrontCamera})")
        } catch (e: Exception) {
            Log.e("StreamingManager", "카메라 전환 실패", e)
        }
    }

    fun isFrontCamera(): Boolean = srtCamera?.isFrontCamera == true

    fun release() {
        try {
            if (srtCamera?.isStreaming == true) srtCamera?.stopStream()
            if (srtCamera?.isOnPreview == true) srtCamera?.stopPreview()
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            srtCamera = null
            needRePrepare = false
        }
    }

    private fun getSrtStreamClient(): com.pedro.library.util.streamclient.SrtStreamClient? {
        return try {
            val field = com.pedro.library.base.Camera1Base::class.java.getDeclaredField("streamClient")
            field.isAccessible = true
            field.get(srtCamera) as? com.pedro.library.util.streamclient.SrtStreamClient
        } catch (e: Exception) {
            null
        }
    }

    override fun onConnectionStarted(url: String) {
        Log.d("StreamingManager", "SRT 연결 시도 중: $url")
    }

    override fun onConnectionSuccess() {
        // 연결 즉시 키프레임(IDR + SPS/PPS) 강제 생성 요청 -> OBS가 즉시 화면을 인식하도록 보장
        srtCamera?.requestKeyFrame()
        Log.d("StreamingManager", "SRT 연결 성공, 키프레임 요청 완료!")
        listener.onConnectionSuccess()
    }

    override fun onConnectionFailed(reason: String) {
        try {
            if (srtCamera?.isStreaming == true) {
                srtCamera?.stopStream()
            }
        } catch (e: Exception) {}
        needRePrepare = true
        Log.e("StreamingManager", "SRT 연결 실패: $reason")
        listener.onConnectionFailed(reason)
    }

    override fun onDisconnect() {
        try {
            if (srtCamera?.isStreaming == true) {
                srtCamera?.stopStream()
            }
        } catch (e: Exception) {}
        needRePrepare = true
        Log.d("StreamingManager", "SRT 연결 해제됨")
        listener.onDisconnect()
    }

    override fun onAuthError() {
        needRePrepare = true
        Log.e("StreamingManager", "SRT 인증 오류 (Passphrase 불일치)")
        listener.onConnectionFailed("Auth Error: Passphrase 불일치")
    }

    override fun onAuthSuccess() {}
    override fun onNewBitrate(bitrate: Long) {}
}
