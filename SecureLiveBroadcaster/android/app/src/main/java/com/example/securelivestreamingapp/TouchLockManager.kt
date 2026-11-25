package com.example.securelivestreamingapp

import android.app.Activity
import android.graphics.Color
import android.graphics.Rect
import android.os.Build
import android.os.CountDownTimer
import android.util.Log
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

class TouchLockManager(private val activity: Activity) {
    var isLocked = false
        private set

    private var lockOverlay: FrameLayout? = null
    private var unlockTimer: CountDownTimer? = null
    private var hintText: TextView? = null
    private val UNLOCK_HOLD_MS = 5000L
    private var onUnlockListener: (() -> Unit)? = null

    fun setOnUnlockListener(l: () -> Unit) { onUnlockListener = l }

    fun lock() {
        if (isLocked) return
        isLocked = true

        // 1. 내비게이션 바(홈 버튼, 멀티태스킹, 뒤로가기) 및 상단바 숨김 (몰입 모드)
        // 주의: startLockTask()는 해제 시 OS 지문인식 잠금화면을 강제하므로 사용하지 않고,
        // 몰입 모드 + 제스처 차단으로 지문인식 없이 매끄럽게 복귀하도록 합니다.
        hideSystemBars()

        // 2. 전체 화면 터치 오버레이 (화면 프리뷰 100% 정상 표시, 터치 이벤트만 완벽 차단)
        showLockOverlay()

        Log.d("TouchLock", "화면 터치 및 내비게이션 바 숨김 잠금 활성화")
    }

    fun unlock() {
        if (!isLocked) return
        isLocked = false

        // 1. 오버레이 제거
        removeLockOverlay()

        // 2. 내비게이션 바 및 상단바 복원 (지문인식 없이 즉시 방송 화면 복구)
        showSystemBars()

        onUnlockListener?.invoke()
        Log.d("TouchLock", "화면 잠금 해제 (지문인식 없이 즉시 복구)")
    }

    private fun hideSystemBars() {
        activity.runOnUiThread {
            try {
                val window = activity.window
                val controller = WindowInsetsControllerCompat(window, window.decorView)
                controller.hide(WindowInsetsCompat.Type.systemBars())
                controller.systemBarsBehavior =
                    WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            } catch (e: Exception) {
                Log.e("TouchLock", "hideSystemBars 에러", e)
            }
        }
    }

    private fun showSystemBars() {
        activity.runOnUiThread {
            try {
                val window = activity.window
                val controller = WindowInsetsControllerCompat(window, window.decorView)
                controller.show(WindowInsetsCompat.Type.systemBars())
            } catch (e: Exception) {
                Log.e("TouchLock", "showSystemBars 에러", e)
            }
        }
    }

    private fun showLockOverlay() {
        val root = activity.window.decorView as ViewGroup

        hintText = TextView(activity).apply {
            text = "🔒 화면 터치 잠금 중\n화면을 5초간 길게 눌러 해제"
            textSize = 17f
            setTextColor(Color.WHITE)
            gravity = Gravity.CENTER
            setBackgroundColor(Color.parseColor("#B3000000"))
            setPadding(40, 24, 40, 24)
        }

        lockOverlay = FrameLayout(activity).apply {
            layoutParams = ViewGroup.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
            // 카메라 프리뷰가 환하게 보이도록 옅은 반투명 레이어 유지
            setBackgroundColor(Color.parseColor("#33000000"))
            isClickable = true
            isFocusable = true

            // Android 10+ 측면 및 하단 시스템 제스처 간섭 차단
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                post {
                    val rects = listOf(Rect(0, 0, width, height))
                    systemGestureExclusionRects = rects
                }
            }

            addView(
                hintText,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT
                ).also { it.gravity = Gravity.CENTER }
            )

            setOnTouchListener(object : View.OnTouchListener {
                override fun onTouch(v: View?, event: MotionEvent?): Boolean {
                    when (event?.action) {
                        MotionEvent.ACTION_DOWN -> startCountdown()
                        MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> cancelCountdown()
                    }
                    return true // 모든 터치를 흡수하여 오작동 차단
                }
            })
        }
        root.addView(lockOverlay)
    }

    private fun startCountdown() {
        unlockTimer?.cancel()
        unlockTimer = object : CountDownTimer(UNLOCK_HOLD_MS, 1000) {
            override fun onTick(ms: Long) {
                val remainingSec = (ms / 1000) + 1
                hintText?.text = "🔓 잠금 해제 중... ${remainingSec}초\n손을 떼지 마세요"
            }
            override fun onFinish() {
                activity.runOnUiThread { unlock() }
            }
        }.start()
    }

    private fun cancelCountdown() {
        unlockTimer?.cancel()
        hintText?.text = "🔒 화면 터치 잠금 중\n화면을 5초간 길게 눌러 해제"
    }

    private fun removeLockOverlay() {
        lockOverlay?.let {
            (activity.window.decorView as ViewGroup).removeView(it)
        }
        lockOverlay = null
        unlockTimer?.cancel()
    }

    fun release() {
        unlockTimer?.cancel()
        if (isLocked) {
            showSystemBars()
            removeLockOverlay()
            isLocked = false
        }
    }
}
