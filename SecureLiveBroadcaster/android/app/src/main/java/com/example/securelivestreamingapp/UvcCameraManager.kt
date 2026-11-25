package com.example.securelivestreamingapp

import android.content.Context
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbManager
import android.util.Log
import com.pedro.common.ConnectChecker
import com.pedro.library.srt.SrtStream
import com.pedro.library.util.sources.audio.MicrophoneSource
import com.pedro.library.view.OpenGlView

/**
 * UVC 외장 USB 카메라 관리자
 * 삼성 갤럭시(One UI) 등에서 Camera HAL 제한 없이 UVCAndroid 엔진을 통해
 * USB 웹캠/캡처카드를 직접 캡처하고 SRT로 송출합니다.
 */
class UvcCameraManager(
    private val context: Context,
    private val openGlView: OpenGlView,
    private val listener: StreamingManager.StreamingListener
) : ConnectChecker {

    private var srtStream: SrtStream? = null
    var isStreaming = false
        private set
    private var needRePrepare = false

    private val usbManager = context.getSystemService(Context.USB_SERVICE) as? UsbManager

    /**
     * 외장 USB (UVC) 비디오 장치가 물리적으로 연결되어 있는지 확인
     */
    fun hasExternalCamera(): Boolean {
        return hasUsbVideoDevice()
    }

    /**
     * USB 포트에 비디오 클래스(웹캠/캡처카드 등) 장치가 연결되어 있는지 확인
     */
    fun hasUsbVideoDevice(): Boolean {
        try {
            val devices = usbManager?.deviceList?.values ?: return false
            for (device in devices) {
                if (device.deviceClass == UsbConstants.USB_CLASS_VIDEO) {
                    return true
                }
                for (i in 0 until device.interfaceCount) {
                    if (device.getInterface(i).interfaceClass == UsbConstants.USB_CLASS_VIDEO) {
                        return true
                    }
                }
            }
        } catch (e: Exception) {
            Log.e("UvcCameraManager", "USB 장치 탐색 에러", e)
        }
        return false
    }

    private fun prepareEncoders(): Boolean {
        val resolutions = listOf(
            Pair(1920, 1080),
            Pair(1280, 720),
            Pair(854, 480),
            Pair(640, 480)
        )
        for ((w, h) in resolutions) {
            val bitrate = if (w >= 1920) 5 * 1024 * 1024 else if (w >= 1280) 3 * 1024 * 1024 else 1024 * 1024
            // StreamBase.prepareVideo(width, height, bitrate, fps, iFrameInterval)
            // 명명된 인자(named arguments)를 사용하여 30bps 클램핑 버그 완전 방지
            val videoOk = srtStream?.prepareVideo(
                width = w,
                height = h,
                bitrate = bitrate,
                fps = 30,
                iFrameInterval = 2
            ) == true
            val audioOk = srtStream?.prepareAudio(
                sampleRate = 44100,
                isStereo = true,
                bitrate = 128 * 1024
            ) == true
            if (videoOk && audioOk) {
                Log.d("UvcCameraManager", "UVC 인코더 초기화 성공 (OpenGL): ${w}x${h}, bitrate=${bitrate}bps, fps=30")
                return true
            } else {
                Log.w("UvcCameraManager", "${w}x${h} UVC 인코더 초기화 실패, 다음 해상도 시도")
            }
        }
        return false
    }

    /**
     * UVC 외장 카메라 프리뷰 시작 (UVCAndroid -> SrtStream -> OpenGlView)
     */
    fun startPreview(): Boolean {
        if (!hasExternalCamera()) {
            Log.w("UvcCameraManager", "연결된 UVC (외장 USB) 카메라를 찾을 수 없음")
            return false
        }

        if (srtStream == null) {
            val uvcSource = CameraUvcSource()
            srtStream = SrtStream(context, this, uvcSource, MicrophoneSource())
        }
        if (srtStream?.isOnPreview == true) return true

        if (prepareEncoders()) {
            Log.d("UvcCameraManager", "UVC 카메라로 프리뷰 시작 (SrtStream + OpenGlView)")
            srtStream?.startPreview(openGlView)
            needRePrepare = false
            return true
        } else {
            Log.e("UvcCameraManager", "UVC 프리뷰 인코더 prepare 실패")
            return false
        }
    }

    fun startStream(url: String, streamId: String, passphrase: String) {
        if (isStreaming) return

        val baseUrl = url.trimEnd('/')
        val finalUrl = if (baseUrl.matches(Regex("srt://[^/]+:\\d+"))) {
            val streamName = if (streamId.isNotEmpty()) streamId else "live"
            "$baseUrl/$streamName"
        } else {
            baseUrl
        }

        try {
            if (!hasExternalCamera()) {
                listener.onConnectionFailed("외장 UVC 카메라가 연결되어 있지 않습니다.")
                return
            }

            if (srtStream == null) {
                val uvcSource = CameraUvcSource()
                srtStream = SrtStream(context, this, uvcSource, MicrophoneSource())
            }

            // 이전 송출 종료 등으로 인코더가 stop 상태이거나 프리뷰가 꺼져 있으면 재초기화
            if (needRePrepare || srtStream?.isOnPreview != true) {
                if (srtStream?.isOnPreview == true) {
                    srtStream?.stopPreview()
                }

                if (!prepareEncoders()) {
                    listener.onConnectionFailed("인코더 초기화 실패")
                    return
                }

                srtStream?.startPreview(openGlView)
                needRePrepare = false
            }

            if (passphrase.length >= 10) {
                (srtStream?.getStreamClient() as? com.pedro.library.util.streamclient.SrtStreamClient)?.setPassphrase(
                    passphrase,
                    com.pedro.srt.srt.packets.control.handshake.EncryptionType.AES128
                )
            }

            Log.d("UvcCameraManager", "UVC SRT 송출 시작 호출: $finalUrl")
            srtStream?.startStream(finalUrl)

        } catch (e: Exception) {
            Log.e("UvcCameraManager", "startStream 오류", e)
            listener.onConnectionFailed("초기화 오류: ${e.message}")
        }
    }

    fun stopStream() {
        try {
            if (isStreaming || srtStream?.isStreaming == true) {
                srtStream?.stopStream()
                isStreaming = false
                needRePrepare = true
                Log.d("UvcCameraManager", "UVC SRT 송출 중단 완료")
            }
        } catch (e: Exception) {
            Log.e("UvcCameraManager", "stopStream 오류", e)
        }
    }

    fun release() {
        stopStream()
        try {
            if (srtStream?.isOnPreview == true) srtStream?.stopPreview()
            srtStream?.release()
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            srtStream = null
            needRePrepare = false
        }
    }

    override fun onConnectionStarted(url: String) {
        Log.d("UvcCameraManager", "UVC 연결 시도 중: $url")
    }

    override fun onConnectionSuccess() {
        isStreaming = true
        // 연결 즉시 키프레임 강제 생성 요청 -> OBS에서 즉시 영상 렌더링
        srtStream?.requestKeyframe()
        Log.d("UvcCameraManager", "UVC SRT 연결 성공, 키프레임 요청 완료!")
        listener.onConnectionSuccess()
    }

    override fun onConnectionFailed(reason: String) {
        try {
            if (srtStream?.isStreaming == true) {
                srtStream?.stopStream()
            }
        } catch (e: Exception) {}
        isStreaming = false
        needRePrepare = true
        Log.e("UvcCameraManager", "UVC SRT 연결 실패: $reason")
        listener.onConnectionFailed(reason)
    }

    override fun onDisconnect() {
        try {
            if (srtStream?.isStreaming == true) {
                srtStream?.stopStream()
            }
        } catch (e: Exception) {}
        isStreaming = false
        needRePrepare = true
        Log.d("UvcCameraManager", "UVC SRT 연결 해제됨")
        listener.onDisconnect()
    }

    override fun onAuthError() {
        isStreaming = false
        needRePrepare = true
        Log.e("UvcCameraManager", "UVC SRT 인증 오류")
        listener.onConnectionFailed("Auth Error: Passphrase 불일치")
    }

    override fun onAuthSuccess() {}
    override fun onNewBitrate(bitrate: Long) {}
}
