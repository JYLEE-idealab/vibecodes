package com.example.securelivestreamingapp

import android.graphics.SurfaceTexture
import android.hardware.usb.UsbDevice
import android.util.Log
import android.view.Surface
import com.herohan.uvcapp.CameraHelper
import com.herohan.uvcapp.ICameraHelper
import com.pedro.library.util.sources.video.VideoSource
import com.serenegiant.usb.Size

/**
 * UVCAndroid (libuvc)를 Pedro RootEncoder의 VideoSource로 연결하는 브릿지 클래스
 * 1080p/720p 고화질 UVC 스트림을 자동 협상하여 선명한 화면을 제공합니다.
 */
class CameraUvcSource : VideoSource() {

    private var cameraHelper: ICameraHelper? = null
    private var isSourceRunning = false
    private var surface: Surface? = null

    override fun create(width: Int, height: Int, fps: Int): Boolean {
        this.width = width
        this.height = height
        this.fps = fps
        created = true
        return true
    }

    private fun findBestSize(sizeList: List<Size>?): Size? {
        if (sizeList.isNullOrEmpty()) return null
        for (s in sizeList) {
            Log.d("CameraUvcSource", "웹캠 지원 해상도: ${s.width}x${s.height}, fps=${s.fps}, type=${s.type}")
        }
        // 1순위: 1920x1080 (FHD, 30fps 이상 우선)
        val fhd = sizeList.filter { it.width == 1920 && it.height == 1080 }.maxByOrNull { it.fps }
        if (fhd != null) return fhd

        // 2순위: 1280x720 (HD, 30fps 이상 우선)
        val hd = sizeList.filter { it.width == 1280 && it.height == 720 }.maxByOrNull { it.fps }
        if (hd != null) return hd

        // 3순위: 16:9 와이드 비율 중 가장 큰 해상도
        val wide = sizeList.filter {
            val ratio = it.width.toFloat() / it.height.toFloat()
            ratio in 1.7f..1.8f
        }.maxByOrNull { it.width * it.height }
        if (wide != null) return wide

        // 4순위: 전체 중 가장 큰 해상도
        return sizeList.maxByOrNull { it.width * it.height }
    }

    override fun start(surfaceTexture: SurfaceTexture) {
        this.surfaceTexture = surfaceTexture
        if (width > 0 && height > 0) {
            surfaceTexture.setDefaultBufferSize(width, height)
            Log.d("CameraUvcSource", "SurfaceTexture 버퍼 크기 설정: ${width}x${height}")
        }
        if (isRunning()) return

        surface = Surface(surfaceTexture)
        cameraHelper = CameraHelper().apply {
            setStateCallback(object : ICameraHelper.StateCallback {
                override fun onAttach(device: UsbDevice) {
                    Log.d("CameraUvcSource", "UVC 기기 감지 (onAttach): ${device.deviceName}")
                    selectDevice(device)
                }

                override fun onDeviceOpen(device: UsbDevice, isFirstOpen: Boolean) {
                    Log.d("CameraUvcSource", "UVC 기기 오픈 성공 (onDeviceOpen): ${device.deviceName}")
                    val sizeList = supportedSizeList
                    val bestSize = findBestSize(sizeList)
                    if (bestSize != null) {
                        Log.d("CameraUvcSource", "최적 UVC 해상도 선택 (openCamera): ${bestSize.width}x${bestSize.height} @ ${bestSize.fps}fps")
                        openCamera(bestSize)
                    } else {
                        openCamera()
                    }
                }

                override fun onCameraOpen(device: UsbDevice) {
                    Log.d("CameraUvcSource", "UVC 카메라 스트림 오픈 완료 (onCameraOpen)")
                    val currentSize = previewSize
                    if (currentSize == null || currentSize.width < 1280) {
                        val sizeList = supportedSizeList
                        val bestSize = findBestSize(sizeList)
                        if (bestSize != null && (currentSize == null || bestSize.width > currentSize.width)) {
                            Log.d("CameraUvcSource", "onCameraOpen에서 고화질 해상도 적용: ${bestSize.width}x${bestSize.height}")
                            previewSize = bestSize
                        }
                    }
                    val finalSize = previewSize
                    Log.d("CameraUvcSource", "최종 UVC 프리뷰 해상도: ${finalSize?.width}x${finalSize?.height}")
                    if (finalSize != null && finalSize.width > 0 && finalSize.height > 0) {
                        this@CameraUvcSource.surfaceTexture?.setDefaultBufferSize(finalSize.width, finalSize.height)
                    }
                    startPreview()
                    surface?.let { addSurface(it, false) }
                }

                override fun onCameraClose(device: UsbDevice) {
                    Log.d("CameraUvcSource", "UVC 카메라 닫힘 (onCameraClose)")
                }

                override fun onDeviceClose(device: UsbDevice) {
                    Log.d("CameraUvcSource", "UVC 기기 닫힘 (onDeviceClose)")
                }

                override fun onDetach(device: UsbDevice) {
                    Log.d("CameraUvcSource", "UVC 기기 분리됨 (onDetach)")
                }

                override fun onCancel(device: UsbDevice) {
                    Log.w("CameraUvcSource", "UVC 권한 취소됨 (onCancel)")
                }
            })

            // 이미 USB에 꽂혀 있는 경우 첫 번째 UVC 장치 자동 선택
            val devices = getDeviceList()
            if (!devices.isNullOrEmpty()) {
                Log.d("CameraUvcSource", "기연결된 UVC 기기 자동 선택: ${devices[0].deviceName}")
                selectDevice(devices[0])
            }
        }
        isSourceRunning = true
    }

    override fun stop() {
        try {
            surface?.let { cameraHelper?.removeSurface(it) }
            surface?.release()
            surface = null
            cameraHelper?.release()
            cameraHelper = null
        } catch (e: Exception) {
            Log.e("CameraUvcSource", "stop 에러", e)
        } finally {
            isSourceRunning = false
        }
    }

    override fun release() {
        stop()
    }

    override fun isRunning(): Boolean = isSourceRunning
}
