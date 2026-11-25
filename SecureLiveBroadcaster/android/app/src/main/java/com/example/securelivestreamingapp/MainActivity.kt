package com.example.securelivestreamingapp

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.view.SurfaceHolder
import android.view.View
import android.view.WindowManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.util.Log
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import android.view.ViewGroup
import com.example.securelivestreamingapp.databinding.ActivityMainBinding
import com.pedro.library.view.OpenGlView

class MainActivity : AppCompatActivity(), StreamingManager.StreamingListener {

    private lateinit var binding: ActivityMainBinding

    // 카메라 매니저
    private var streamingManager: StreamingManager? = null
    private var uvcCameraManager: UvcCameraManager? = null
    private var isUvcMode = false
    private var isSurfaceReady = false

    // 신규 매니저
    private lateinit var audioRoutingManager: AudioRoutingManager
    private lateinit var touchLockManager: TouchLockManager
    private var obsWebSocketManager: ObsWebSocketManager? = null
    private var chzzkChatManager: ChzzkChatManager? = null
    private lateinit var chzzkStudioManager: ChzzkStudioManager
    private lateinit var ttsManager: TtsManager

    // OBS 장면/소스 제어 및 미니 모니터(PiP) 상태
    private var currentObsSceneName: String = ""
    private val obsScenesList = mutableListOf<String>()
    private val currentSceneItems = mutableListOf<ObsSceneItem>()
    private var isProgramMonitorRunning = false
    private var lastMonitorFrameTime: Long = 0L
    private val monitorHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val monitorWatchdogRunnable = object : Runnable {
        override fun run() {
            if (isProgramMonitorRunning && obsWebSocketManager?.isConnected == true) {
                if (System.currentTimeMillis() - lastMonitorFrameTime > 500L) {
                    requestNextMonitorFrame()
                }
                monitorHandler.postDelayed(this, 500L)
            }
        }
    }

    // 치지직 방송 송출 시간 타이머 상태
    private var isChzzkStreaming = false
    private var chzzkStreamStartTimeMs: Long = 0L
    private val chzzkTimerHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private val chzzkTimerRunnable = object : Runnable {
        override fun run() {
            if (isChzzkStreaming && chzzkStreamStartTimeMs > 0L) {
                val elapsedSeconds = (android.os.SystemClock.elapsedRealtime() - chzzkStreamStartTimeMs) / 1000
                val formatted = formatTime(elapsedSeconds)
                binding.tvChzzkLiveTime.text = "치지직 LIVE $formatted"
                binding.tvObsStreamDuration.text = "치지직 방송 시간: $formatted"
                chzzkTimerHandler.postDelayed(this, 1000L)
            }
        }
    }

    // 카메라 화면 모니터링(프리뷰) ON/OFF 상태 (SRT 송출 무간섭)
    private var isCameraPreviewMonitoringEnabled = true

    // 자동 재연결(Auto-Reconnect) 상태 관리 (OBS 장면 전환 및 일시적 망 순단 자동 복구)
    private var isUserInitiatedStop = true
    private var isReconnecting = false
    private var reconnectAttempts = 0
    private val maxReconnectAttempts = 15 // 최대 15회 시도 (~30초간 자동 복구 시도)
    private val reconnectHandler = android.os.Handler(android.os.Looper.getMainLooper())
    private var reconnectRunnable: Runnable? = null

    private val PERMISSIONS: Array<String>
        get() = mutableListOf(
            Manifest.permission.CAMERA,
            Manifest.permission.RECORD_AUDIO
        ).apply {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                add(Manifest.permission.BLUETOOTH_CONNECT)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }.toTypedArray()
    private val PERMISSION_REQ_CODE = 100

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // ── 스마트폰 화면 자동 꺼짐 및 잠금화면 전환 완전 방지 (Keep Screen On) ──
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        // ── 잠금화면 위에서도 화면 표시 및 켜짐 유지 (Android 8.1+ / O_MR1) ──
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O_MR1) {
            setShowWhenLocked(true)
            setTurnScreenOn(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(
                WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED or
                WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
            )
        }

        // ── 프로세스 크래시 및 재시작 방지 (OOM 및 백그라운드 스레드 예외 방어) ──
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { thread, throwable ->
            android.util.Log.e("MainActivity", "비정상 종료 방어 (스레드: ${thread.name})", throwable)
            if (throwable is OutOfMemoryError) {
                System.gc()
            }
            if (thread.name.contains("WebSocket") || thread.name.contains("DefaultExecutor")) {
                android.util.Log.w("MainActivity", "백그라운드 스레드 오류 자동 격리 완료 (방송 스트림 유지)")
            } else {
                defaultHandler?.uncaughtException(thread, throwable)
            }
        }

        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        SecurityManager.init(this)

        audioRoutingManager = AudioRoutingManager(this)
        touchLockManager = TouchLockManager(this)
        touchLockManager.setOnUnlockListener {
            binding.btnTouchLock.text = "🔒"
        }

        // ── 잠금 상태 시 뒤로가기 제스처/버튼 완전 차단 ──
        onBackPressedDispatcher.addCallback(this, object : androidx.activity.OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (touchLockManager.isLocked) {
                    // 잠금 상태에서는 뒤로가기 동작 무시
                    return
                }
                isEnabled = false
                onBackPressedDispatcher.onBackPressed()
                isEnabled = true
            }
        })

        ttsManager = TtsManager(this)
        ttsManager.init()

        loadSavedConfig()

        // ── USB UVC 카메라 연결/해제 감지 리시버 등록 ──
        val usbFilter = android.content.IntentFilter().apply {
            addAction(android.hardware.usb.UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(android.hardware.usb.UsbManager.ACTION_USB_DEVICE_DETACHED)
        }
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            registerReceiver(usbReceiver, usbFilter, android.content.Context.RECEIVER_EXPORTED)
        } else {
            registerReceiver(usbReceiver, usbFilter)
        }

        if (checkPermissions()) setupApp()
        else ActivityCompat.requestPermissions(this, PERMISSIONS, PERMISSION_REQ_CODE)
    }

    private val usbReceiver = object : android.content.BroadcastReceiver() {
        override fun onReceive(context: android.content.Context?, intent: android.content.Intent?) {
            when (intent?.action) {
                android.hardware.usb.UsbManager.ACTION_USB_DEVICE_ATTACHED -> {
                    Toast.makeText(
                        this@MainActivity,
                        "🔌 USB 외장 장치가 연결되었습니다.\nUVC 카메라인 경우 [내장 카메라] 버튼을 눌러 모드를 전환하세요.",
                        Toast.LENGTH_LONG
                    ).show()
                }
                android.hardware.usb.UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                    if (isUvcMode) {
                        Toast.makeText(
                            this@MainActivity,
                            "⚠️ UVC 카메라 연결이 해제되어 내장 카메라로 자동 전환합니다.",
                            Toast.LENGTH_LONG
                        ).show()
                        switchCameraMode()
                    }
                }
            }
        }
    }

    private fun loadSavedConfig() {
        binding.etServerIp.setText(SecurityManager.getSrtUrl())
        binding.etStreamId.setText(SecurityManager.getSrtStreamId())
        binding.etPassphrase.setText(SecurityManager.getSrtPassphrase())

        binding.etObsHost.setText(SecurityManager.getObsHost())
        binding.etObsPort.setText(SecurityManager.getObsPort().toString())
        binding.etObsPassword.setText(SecurityManager.getObsPassword())

        binding.etChzzkChannelId.setText(SecurityManager.getChzzkChannelId())
    }

    private fun setupApp() {
        streamingManager = StreamingManager(this, binding.surfaceView, this)
        streamingManager?.initMode(false)
        uvcCameraManager = UvcCameraManager(this, binding.surfaceView, this)

        binding.surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                isSurfaceReady = true
                android.util.Log.d("MainActivity", "surfaceCreated 콜백 수신 -> 카메라 프리뷰 시작")
                startCameraPreview()
            }
            override fun surfaceChanged(holder: SurfaceHolder, format: Int, w: Int, h: Int) {
                isSurfaceReady = true
            }
            override fun surfaceDestroyed(holder: SurfaceHolder) {
                isSurfaceReady = false
                android.util.Log.d("MainActivity", "surfaceDestroyed 호출됨 (송출 세션 유지)")
            }
        })

        // 중요: 새 기기에서 권한 허용 팝업 등으로 surfaceCreated가 이미 지난 경우 즉시 프리뷰 실행
        if (binding.surfaceView.holder.surface.isValid) {
            isSurfaceReady = true
            android.util.Log.d("MainActivity", "Surface가 이미 유효함 -> 즉시 카메라 프리뷰 시작")
            startCameraPreview()
        }

        // ── 설정 암호화 저장 ──
        binding.btnSaveConfig.setOnClickListener {
            val url = binding.etServerIp.text.toString().trim()
            val streamId = binding.etStreamId.text.toString().trim()
            val pass = binding.etPassphrase.text.toString().trim()

            SecurityManager.saveSrtConfig(url, streamId, pass)
            Toast.makeText(this, "SRT 설정이 암호화 저장되었습니다.", Toast.LENGTH_SHORT).show()
        }

        binding.btnSaveObsConfig.setOnClickListener {
            val obsHost = binding.etObsHost.text.toString().trim()
            val obsPort = binding.etObsPort.text.toString().trim().toIntOrNull() ?: 4455
            val obsPass = binding.etObsPassword.text.toString().trim()

            SecurityManager.saveObsConfig(obsHost, obsPort, obsPass)
            Toast.makeText(this, "OBS WebSocket 설정이 저장되었습니다.", Toast.LENGTH_SHORT).show()
        }

        binding.btnSaveChzzkConfig.setOnClickListener {
            val chzzkChannel = binding.etChzzkChannelId.text.toString().trim()

            SecurityManager.saveChzzkConfig(chzzkChannel)
            Toast.makeText(this, "치지직 채팅 설정이 저장되었습니다.", Toast.LENGTH_SHORT).show()
        }

        // ── 오디오 모드 선택 ──
        binding.rgAudioMode.setOnCheckedChangeListener { _, checkedId ->
            val mode = when (checkedId) {
                R.id.rbBluetooth -> AudioMode.BLUETOOTH
                R.id.rbUvcWired  -> AudioMode.UVC_WIRED
                else             -> AudioMode.PHONE
            }
            audioRoutingManager.applyMode(mode)
            Toast.makeText(this, "오디오 모드 적용: ${mode.name}", Toast.LENGTH_SHORT).show()
        }

        // ── 카메라 모드 토글 (내장 ↔ UVC) ──
        binding.btnToggleMode.setOnClickListener { switchCameraMode() }

        // ── 카메라 전/후면 전환 ──
        binding.btnFlipCamera.setOnClickListener {
            if (isUvcMode) {
                Toast.makeText(this, "UVC 외장 카메라 모드에서는 전/후면 전환이 지원되지 않습니다.", Toast.LENGTH_SHORT).show()
            } else {
                streamingManager?.switchCamera()
                val isFront = streamingManager?.isFrontCamera() == true
                Toast.makeText(this, if (isFront) "전면 카메라 전환" else "후면 카메라 전환", Toast.LENGTH_SHORT).show()
            }
        }

        // ── 방송 시작/종료 ──
        binding.btnStream.setOnClickListener { toggleStreaming() }

        // ── 터치 잠금 ──
        binding.btnTouchLock.setOnClickListener {
            if (touchLockManager.isLocked) {
                touchLockManager.unlock()
                binding.btnTouchLock.text = "🔒"
            } else {
                touchLockManager.lock()
                binding.btnTouchLock.text = "🔓"
                Toast.makeText(this, "화면 터치 잠금 활성화 (5초 길게 눌러 해제)", Toast.LENGTH_SHORT).show()
            }
        }

        // ── 카메라 화면 모니터링(프리뷰) ON/OFF 절전 토글 ──
        binding.btnTogglePreviewMonitor.setOnClickListener { toggleCameraPreviewMonitor() }
        binding.layoutCameraPreviewCover.setOnClickListener {
            if (!touchLockManager.isLocked) {
                toggleCameraPreviewMonitor()
            }
        }

        // ── 좌측 제어 메뉴 열기/닫기 토글 ──
        binding.btnToggleMenu.setOnClickListener { toggleConfigMenu() }
        binding.btnCloseMenu.setOnClickListener { toggleConfigMenu() }

        // ── OBS WebSocket 제어 ──
        setupObsWebSocket()

        // ── 치지직 채팅 TTS 제어 ──
        setupChzzkChat()

        // ── 치지직 방송 관리 (제목/태그) & 네이버 계정 연동 ──
        setupChzzkStudio()
    }

    private fun setupObsWebSocket() {
        binding.btnObsConnect.setOnClickListener {
            if (obsWebSocketManager?.isConnected == true) {
                obsWebSocketManager?.disconnect()
                binding.btnObsConnect.text = "OBS 연결"
                binding.tvObsStatus.text = "OBS: 연결 끊김"
                binding.tvObsStatus.setTextColor(Color.parseColor("#FFCC00"))
                return@setOnClickListener
            }

            val host = binding.etObsHost.text.toString().trim()
            val port = binding.etObsPort.text.toString().trim().toIntOrNull() ?: 4455
            val pass = binding.etObsPassword.text.toString().trim()

            if (host.isEmpty()) {
                Toast.makeText(this, "OBS PC IP를 입력하세요", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            // 연결 시 자동 저장
            SecurityManager.saveObsConfig(host, port, pass)

            binding.tvObsStatus.text = "OBS: 연결 시도 중..."
            binding.tvObsStatus.setTextColor(Color.parseColor("#FFCC00"))

            obsWebSocketManager = ObsWebSocketManager(host, port, pass, object : ObsWebSocketManager.ObsListener {
                override fun onConnected() {
                    runOnUiThread {
                        binding.tvObsStatus.text = "OBS: 연결됨 (대기)"
                        binding.tvObsStatus.setTextColor(Color.GREEN)
                        binding.btnObsConnect.text = "OBS 해제"
                        binding.layoutObsSceneControl.visibility = View.VISIBLE
                        Toast.makeText(this@MainActivity, "OBS WebSocket 연결 성공", Toast.LENGTH_SHORT).show()
                    }
                }

                override fun onDisconnected() {
                    runOnUiThread {
                        binding.tvObsStatus.text = "OBS: 연결 끊김"
                        binding.tvObsStatus.setTextColor(Color.parseColor("#FFCC00"))
                        binding.btnObsConnect.text = "OBS 연결"
                        binding.layoutObsSceneControl.visibility = View.GONE
                        stopProgramMonitor()
                        if (isChzzkStreaming) {
                            stopChzzkTimer()
                        }
                    }
                }

                override fun onStreamingStarted() {
                    runOnUiThread {
                        binding.tvObsStatus.text = "OBS: 🔴 방송 송출 중!"
                        binding.tvObsStatus.setTextColor(Color.RED)
                        startChzzkTimer()
                        Toast.makeText(this@MainActivity, "OBS 치지직 방송 시작됨", Toast.LENGTH_SHORT).show()
                    }
                }

                override fun onStreamingStopped() {
                    runOnUiThread {
                        binding.tvObsStatus.text = "OBS: 연결됨 (대기)"
                        binding.tvObsStatus.setTextColor(Color.GREEN)
                        stopChzzkTimer()
                        Toast.makeText(this@MainActivity, "OBS 치지직 방송 종료됨", Toast.LENGTH_SHORT).show()
                    }
                }

                override fun onError(msg: String) {
                    runOnUiThread {
                        binding.tvObsStatus.text = "OBS 오류"
                        binding.tvObsStatus.setTextColor(Color.RED)
                        Toast.makeText(this@MainActivity, msg, Toast.LENGTH_LONG).show()
                    }
                }

                override fun onStreamingStatus(isStreaming: Boolean, timecode: String?, durationMs: Long?) {
                    runOnUiThread {
                        if (isStreaming) {
                            binding.tvObsStatus.text = "OBS: 🔴 방송 송출 중!"
                            binding.tvObsStatus.setTextColor(Color.RED)
                            if (!isChzzkStreaming) {
                                if (durationMs != null && durationMs > 0L) {
                                    chzzkStreamStartTimeMs = android.os.SystemClock.elapsedRealtime() - durationMs
                                }
                                startChzzkTimer()
                            }
                        } else {
                            binding.tvObsStatus.text = "OBS: 연결됨 (대기)"
                            binding.tvObsStatus.setTextColor(Color.GREEN)
                            if (isChzzkStreaming) {
                                stopChzzkTimer()
                            }
                        }
                    }
                }

                override fun onSceneListReceived(currentScene: String, scenes: List<String>) {
                    runOnUiThread {
                        currentObsSceneName = currentScene
                        obsScenesList.clear()
                        obsScenesList.addAll(scenes)
                        updateSceneSpinner(currentScene, scenes)
                    }
                }

                override fun onSceneItemListReceived(sceneName: String, items: List<ObsSceneItem>) {
                    runOnUiThread {
                        updateSourceItemsView(sceneName, items)
                    }
                }

                override fun onCurrentSceneChanged(sceneName: String) {
                    runOnUiThread {
                        currentObsSceneName = sceneName
                        val idx = obsScenesList.indexOf(sceneName)
                        if (idx != -1 && binding.spObsScenes.selectedItemPosition != idx) {
                            binding.spObsScenes.setSelection(idx)
                        }
                    }
                }

                override fun onSceneItemStateChanged(sceneName: String, itemId: Int, enabled: Boolean) {
                    runOnUiThread {
                        if (sceneName == currentObsSceneName) {
                            updateSingleSourceItem(itemId, enabled)
                        }
                    }
                }

                override fun onScreenshotReceived(bitmap: Bitmap) {
                    runOnUiThread {
                        if (isProgramMonitorRunning) {
                            lastMonitorFrameTime = System.currentTimeMillis()
                            try {
                                binding.ivProgramMonitor.setImageBitmap(bitmap)
                                binding.ivProgramMonitor.invalidate()
                            } catch (oom: OutOfMemoryError) {
                                System.gc()
                            }
                            // 부드러운 ~15 FPS 유지 및 힙 메모리 안정성 극대화를 위해 65ms 딜레이 후 다음 프레임 요청
                            monitorHandler.postDelayed({
                                if (isProgramMonitorRunning) {
                                    requestNextMonitorFrame()
                                }
                            }, 65L)
                        }
                    }
                }
            })

            obsWebSocketManager?.connect()
        }

        binding.btnObsStartStream.setOnClickListener {
            if (obsWebSocketManager?.isConnected == true) {
                obsWebSocketManager?.startStreaming()
            } else {
                Toast.makeText(this, "OBS에 먼저 연결하세요", Toast.LENGTH_SHORT).show()
            }
        }

        binding.btnObsStopStream.setOnClickListener {
            if (obsWebSocketManager?.isConnected == true) {
                obsWebSocketManager?.stopStreaming()
            } else {
                Toast.makeText(this, "OBS에 먼저 연결하세요", Toast.LENGTH_SHORT).show()
            }
        }

        // 송출 모니터 토글 및 닫기 버튼
        binding.btnToggleProgramMonitor.setOnClickListener { toggleProgramMonitor() }
        binding.btnCloseMonitor.setOnClickListener { stopProgramMonitor() }
    }

    private fun Int.dpToPx(): Int = (this * resources.displayMetrics.density).toInt()

    private fun updateSceneSpinner(currentScene: String, scenes: List<String>) {
        if (scenes.isEmpty()) return
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, scenes)
        binding.spObsScenes.adapter = adapter
        val idx = scenes.indexOf(currentScene)
        if (idx != -1) {
            binding.spObsScenes.setSelection(idx)
        }
        binding.spObsScenes.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                if (touchLockManager.isLocked) return
                if (position in scenes.indices) {
                    val selected = scenes[position]
                    if (selected != currentObsSceneName) {
                        currentObsSceneName = selected
                        obsWebSocketManager?.setCurrentProgramScene(selected)
                        obsWebSocketManager?.getSceneItemList(selected)
                        Toast.makeText(this@MainActivity, "장면 전환: $selected", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            override fun onNothingSelected(parent: AdapterView<*>?) {}
        }
    }

    private fun updateSourceItemsView(sceneName: String, items: List<ObsSceneItem>) {
        currentSceneItems.clear()
        currentSceneItems.addAll(items)
        binding.layoutObsSourceItems.removeAllViews()

        if (items.isEmpty()) {
            val emptyTv = TextView(this).apply {
                text = "장면에 소스가 없습니다"
                setTextColor(Color.parseColor("#999999"))
                textSize = 11f
                setPadding(4, 4, 4, 4)
            }
            binding.layoutObsSourceItems.addView(emptyTv)
            return
        }

        for (item in items) {
            val row = LinearLayout(this).apply {
                orientation = LinearLayout.HORIZONTAL
                gravity = android.view.Gravity.CENTER_VERTICAL
                setPadding(0, 3.dpToPx(), 0, 3.dpToPx())
                tag = item.id
            }

            val btnEye = Button(this).apply {
                text = if (item.enabled) "👁️" else "🚫"
                textSize = 12f
                layoutParams = LinearLayout.LayoutParams(42.dpToPx(), 32.dpToPx()).apply {
                    marginEnd = 6.dpToPx()
                }
                setPadding(0, 0, 0, 0)
                setBackgroundColor(if (item.enabled) Color.parseColor("#2E7D32") else Color.parseColor("#616161"))
                setOnClickListener {
                    if (touchLockManager.isLocked) return@setOnClickListener
                    val newState = !item.enabled
                    item.enabled = newState
                    text = if (newState) "👁️" else "🚫"
                    setBackgroundColor(if (newState) Color.parseColor("#2E7D32") else Color.parseColor("#616161"))
                    obsWebSocketManager?.setSceneItemEnabled(currentObsSceneName, item.id, newState)
                }
            }

            val tvName = TextView(this).apply {
                text = item.name
                setTextColor(if (item.enabled) Color.WHITE else Color.parseColor("#888888"))
                textSize = 12f
                layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
                maxLines = 1
                ellipsize = android.text.TextUtils.TruncateAt.END
            }

            row.addView(btnEye)
            row.addView(tvName)
            binding.layoutObsSourceItems.addView(row)
        }
    }

    private fun updateSingleSourceItem(itemId: Int, enabled: Boolean) {
        val item = currentSceneItems.find { it.id == itemId }
        item?.enabled = enabled
        val row = binding.layoutObsSourceItems.findViewWithTag<LinearLayout>(itemId) ?: return
        val btnEye = row.getChildAt(0) as? Button
        val tvName = row.getChildAt(1) as? TextView
        btnEye?.text = if (enabled) "👁️" else "🚫"
        btnEye?.setBackgroundColor(if (enabled) Color.parseColor("#2E7D32") else Color.parseColor("#616161"))
        tvName?.setTextColor(if (enabled) Color.WHITE else Color.parseColor("#888888"))
    }

    private fun toggleProgramMonitor() {
        if (touchLockManager.isLocked) return
        if (isProgramMonitorRunning) {
            stopProgramMonitor()
        } else {
            startProgramMonitor()
        }
    }

    private fun requestNextMonitorFrame() {
        if (!isProgramMonitorRunning || obsWebSocketManager?.isConnected != true) return
        val targetSource = if (currentObsSceneName.isNotEmpty()) {
            currentObsSceneName
        } else if (obsScenesList.isNotEmpty()) {
            obsScenesList[0]
        } else {
            "Program"
        }
        obsWebSocketManager?.requestProgramScreenshot(targetSource)
    }

    private fun startProgramMonitor() {
        if (obsWebSocketManager?.isConnected != true) {
            Toast.makeText(this, "OBS 연결 후 모니터링을 켤 수 있습니다.", Toast.LENGTH_SHORT).show()
            return
        }
        isProgramMonitorRunning = true
        lastMonitorFrameTime = System.currentTimeMillis()
        binding.cardProgramMonitor.visibility = View.VISIBLE
        binding.btnToggleProgramMonitor.text = "📺 송출 화면 모니터 (ON)"
        binding.btnToggleProgramMonitor.setBackgroundColor(Color.parseColor("#2E7D32"))
        monitorHandler.removeCallbacksAndMessages(null)
        monitorHandler.post(monitorWatchdogRunnable)
        requestNextMonitorFrame()
        Toast.makeText(this, "OBS 송출 화면 실시간 모니터링 시작", Toast.LENGTH_SHORT).show()
    }

    private fun stopProgramMonitor() {
        isProgramMonitorRunning = false
        monitorHandler.removeCallbacksAndMessages(null)
        try {
            binding.ivProgramMonitor.setImageDrawable(null)
        } catch (e: Exception) {
            e.printStackTrace()
        }
        binding.cardProgramMonitor.visibility = View.GONE
        binding.btnToggleProgramMonitor.text = "📺 송출 화면 모니터 (OFF)"
        binding.btnToggleProgramMonitor.setBackgroundColor(Color.parseColor("#0277BD"))
    }

    private fun toggleConfigMenu() {
        if (touchLockManager.isLocked) return
        val isVisible = binding.configPanel.visibility == View.VISIBLE
        if (isVisible) {
            binding.configPanel.visibility = View.GONE
            binding.btnToggleMenu.text = "☰ 메뉴 열기"
            binding.btnToggleMenu.setBackgroundColor(Color.parseColor("#CC263238"))
        } else {
            binding.configPanel.visibility = View.VISIBLE
            binding.btnToggleMenu.text = "✕ 메뉴 닫기"
            binding.btnToggleMenu.setBackgroundColor(Color.parseColor("#CC37474F"))
        }
    }

    private fun toggleCameraPreviewMonitor() {
        if (touchLockManager.isLocked) return
        isCameraPreviewMonitoringEnabled = !isCameraPreviewMonitoringEnabled
        if (isCameraPreviewMonitoringEnabled) {
            binding.layoutCameraPreviewCover.visibility = View.GONE
            binding.btnTogglePreviewMonitor.text = "👁️ 화면 ON"
            binding.btnTogglePreviewMonitor.setBackgroundColor(Color.parseColor("#2E7D32"))
            Toast.makeText(this, "카메라 화면 모니터링 켜짐", Toast.LENGTH_SHORT).show()
        } else {
            binding.layoutCameraPreviewCover.visibility = View.VISIBLE
            binding.btnTogglePreviewMonitor.text = "🚫 화면 OFF"
            binding.btnTogglePreviewMonitor.setBackgroundColor(Color.parseColor("#616161"))
            Toast.makeText(this, "화면 모니터링 절전 OFF (SRT 1080p 송출은 정상 작동)", Toast.LENGTH_SHORT).show()
        }
    }

    private fun startChzzkTimer() {
        if (isChzzkStreaming && chzzkStreamStartTimeMs > 0L) return
        isChzzkStreaming = true
        if (chzzkStreamStartTimeMs == 0L) {
            chzzkStreamStartTimeMs = android.os.SystemClock.elapsedRealtime()
        }
        binding.tvChzzkLiveDot.text = "🔴"
        binding.tvChzzkLiveTime.setTextColor(Color.parseColor("#00E676"))
        binding.cardChzzkLiveTimer.setCardBackgroundColor(Color.parseColor("#EE1B2B1B"))
        chzzkTimerHandler.removeCallbacks(chzzkTimerRunnable)
        chzzkTimerHandler.post(chzzkTimerRunnable)
    }

    private fun stopChzzkTimer() {
        val elapsedSeconds = if (chzzkStreamStartTimeMs > 0L) {
            (android.os.SystemClock.elapsedRealtime() - chzzkStreamStartTimeMs) / 1000
        } else 0L
        isChzzkStreaming = false
        chzzkTimerHandler.removeCallbacks(chzzkTimerRunnable)
        chzzkStreamStartTimeMs = 0L

        val formattedTime = formatTime(elapsedSeconds)
        binding.tvChzzkLiveDot.text = "⚪"
        binding.tvChzzkLiveTime.text = if (elapsedSeconds > 0) "치지직: 대기 중 (최근: $formattedTime)" else "치지직: 대기 중"
        binding.tvChzzkLiveTime.setTextColor(Color.parseColor("#CCCCCC"))
        binding.cardChzzkLiveTimer.setCardBackgroundColor(Color.parseColor("#CC111111"))
        binding.tvObsStreamDuration.text = if (elapsedSeconds > 0) "치지직 방송 시간: 대기 중 ($formattedTime)" else "치지직 방송 시간: 대기 중 (00:00:00)"
    }

    private fun formatTime(totalSeconds: Long): String {
        val hours = totalSeconds / 3600
        val minutes = (totalSeconds % 3600) / 60
        val seconds = totalSeconds % 60
        return String.format(java.util.Locale.getDefault(), "%02d:%02d:%02d", hours, minutes, seconds)
    }

    private fun setupChzzkChat() {
        binding.btnChzzkConnect.setOnClickListener {
            val channelId = binding.etChzzkChannelId.text.toString().trim()
            if (channelId.isEmpty()) {
                Toast.makeText(this, "치지직 채널 ID를 입력하세요", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            // 연결 시 자동 저장
            SecurityManager.saveChzzkConfig(channelId)

            binding.tvChatStatus.text = "채팅: 연결 중..."
            binding.tvChatStatus.setTextColor(Color.parseColor("#FFCC00"))

            chzzkChatManager?.disconnect()
            chzzkChatManager = ChzzkChatManager(channelId, object : ChzzkChatManager.ChatListener {
                override fun onChatMessage(userName: String, message: String) {
                    runOnUiThread {
                        binding.tvChatStatus.text = "[$userName] $message"
                        binding.tvChatStatus.setTextColor(Color.WHITE)
                    }
                    // 네이티브 TTS 음성 출력
                    ttsManager.speak("$userName. $message")
                }

                override fun onConnected() {
                    runOnUiThread {
                        binding.tvChatStatus.text = "채팅: 실시간 수신 중"
                        binding.tvChatStatus.setTextColor(Color.GREEN)
                        Toast.makeText(this@MainActivity, "치지직 채팅 연결 완료", Toast.LENGTH_SHORT).show()
                    }
                    ttsManager.speak("치지직 채팅이 연결되었습니다.")
                }

                override fun onDisconnected() {
                    runOnUiThread {
                        binding.tvChatStatus.text = "채팅: 연결 끊김"
                        binding.tvChatStatus.setTextColor(Color.parseColor("#FFCC00"))
                    }
                }

                override fun onError(msg: String) {
                    runOnUiThread {
                        binding.tvChatStatus.text = "채팅 에러: $msg"
                        binding.tvChatStatus.setTextColor(Color.RED)
                        Toast.makeText(this@MainActivity, msg, Toast.LENGTH_LONG).show()
                    }
                }
            })

            chzzkChatManager?.connect()
        }

        binding.btnChzzkDisconnect.setOnClickListener {
            chzzkChatManager?.disconnect()
            ttsManager.stop()
            binding.tvChatStatus.text = "채팅: 종료됨"
            binding.tvChatStatus.setTextColor(Color.parseColor("#FFCC00"))
        }

        binding.btnTtsTest.setOnClickListener {
            ttsManager.speak("치지직 티티에스 소리 테스트입니다. 음성이 잘 들리십니까?")
            Toast.makeText(this, "TTS 음성 출력 테스트 중...", Toast.LENGTH_SHORT).show()
        }
    }

    private fun setupChzzkStudio() {
        chzzkStudioManager = ChzzkStudioManager(this)
        updateNaverLoginUi()

        binding.btnNaverLogin.setOnClickListener {
            showNaverLoginDialog()
        }

        binding.btnNaverLogout.setOnClickListener {
            SecurityManager.clearNaverAccount()
            android.webkit.CookieManager.getInstance().removeAllCookies(null)
            updateNaverLoginUi()
            Toast.makeText(this, "네이버 계정 로그아웃 완료", Toast.LENGTH_SHORT).show()
        }

        binding.btnRefreshLiveSetting.setOnClickListener {
            loadChzzkLiveSetting(showToast = true)
        }

        binding.btnApplyLiveSetting.setOnClickListener {
            applyChzzkLiveSetting()
        }

        // 로그인된 상태라면 자동으로 현재 방송 정보 로드
        if (SecurityManager.isNaverLoggedIn()) {
            loadChzzkLiveSetting(showToast = false)
        }
    }

    private fun updateNaverLoginUi() {
        val isLoggedIn = SecurityManager.isNaverLoggedIn()
        val nickname = SecurityManager.getNaverNickname()

        if (isLoggedIn) {
            val displayName = if (nickname.isNotEmpty()) nickname else "로그인됨"
            binding.tvNaverAccountStatus.text = "네이버: 🟢 $displayName"
            binding.tvNaverAccountStatus.setTextColor(Color.parseColor("#00E676"))
            binding.btnNaverLogin.visibility = View.GONE
            binding.btnNaverLogout.visibility = View.VISIBLE
            binding.layoutLiveSettingPanel.visibility = View.VISIBLE
        } else {
            binding.tvNaverAccountStatus.text = "네이버: 미연동"
            binding.tvNaverAccountStatus.setTextColor(Color.parseColor("#AAAAAA"))
            binding.btnNaverLogin.visibility = View.VISIBLE
            binding.btnNaverLogout.visibility = View.GONE
            binding.layoutLiveSettingPanel.visibility = View.GONE
        }
    }

    private fun showNaverLoginDialog() {
        val dialog = android.app.Dialog(this)
        dialog.requestWindowFeature(android.view.Window.FEATURE_NO_TITLE)

        val density = resources.displayMetrics.density
        val pad16 = (16 * density).toInt()
        val pad10 = (10 * density).toInt()

        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            setBackgroundColor(Color.WHITE)
        }

        val titleBar = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            setPadding(pad16, pad10, pad16, pad10)
            setBackgroundColor(Color.parseColor("#03C75A"))
            gravity = android.view.Gravity.CENTER_VERTICAL
        }

        val tvTitle = TextView(this).apply {
            text = "네이버 계정 로그인 (치지직 연동)"
            setTextColor(Color.WHITE)
            textSize = 15f
            setTypeface(null, android.graphics.Typeface.BOLD)
            layoutParams = LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f)
        }

        val btnClose = Button(this).apply {
            text = "✕"
            setTextColor(Color.WHITE)
            setBackgroundColor(Color.TRANSPARENT)
            textSize = 18f
            setOnClickListener { dialog.dismiss() }
        }

        titleBar.addView(tvTitle)
        titleBar.addView(btnClose)
        layout.addView(titleBar)

        val webView = android.webkit.WebView(this).apply {
            layoutParams = LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.userAgentString = "Mozilla/5.0 (Linux; Android 14; Mobile) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        }

        webView.webViewClient = object : android.webkit.WebViewClient() {
            override fun onPageFinished(view: android.webkit.WebView?, url: String?) {
                super.onPageFinished(view, url)
                val cookie = android.webkit.CookieManager.getInstance().getCookie(url) ?: ""
                if (cookie.contains("NID_AUT") && cookie.contains("NID_SES")) {
                    Log.d("MainActivity", "네이버 로그인 세션 쿠키 획득 성공!")
                    SecurityManager.saveNaverAccount(cookie)
                    dialog.dismiss()
                    Toast.makeText(this@MainActivity, "🟢 네이버 계정 연동 성공!", Toast.LENGTH_SHORT).show()

                    // 닉네임 조회 & 방송 정보 자동 로드
                    chzzkStudioManager.fetchUserNickname { nickname ->
                        if (nickname.isNotEmpty()) {
                            SecurityManager.saveNaverAccount(cookie, nickname)
                        }
                        updateNaverLoginUi()
                        loadChzzkLiveSetting(showToast = false)
                    }
                }
            }
        }

        webView.loadUrl("https://nid.naver.com/nidlogin.login")
        layout.addView(webView)

        dialog.setContentView(layout)
        dialog.window?.setLayout(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
        dialog.show()
    }

    private fun loadChzzkLiveSetting(showToast: Boolean) {
        val channelId = binding.etChzzkChannelId.text.toString().trim()
        if (channelId.isEmpty()) {
            if (showToast) Toast.makeText(this, "치지직 채널 ID를 먼저 입력하세요", Toast.LENGTH_SHORT).show()
            return
        }

        chzzkStudioManager.fetchLiveSetting(channelId) { result ->
            result.onSuccess { setting ->
                binding.etLiveTitle.setText(setting.title)
                binding.etLiveTags.setText(setting.tags.joinToString(", "))
                if (showToast) {
                    Toast.makeText(this, "방송 정보를 불러왔습니다.", Toast.LENGTH_SHORT).show()
                }
            }.onFailure { ex ->
                if (showToast) {
                    Toast.makeText(this, "방송 정보 로드 실패: ${ex.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    private fun applyChzzkLiveSetting() {
        val channelId = binding.etChzzkChannelId.text.toString().trim()
        val title = binding.etLiveTitle.text.toString().trim()
        val tagsStr = binding.etLiveTags.text.toString().trim()

        if (channelId.isEmpty()) {
            Toast.makeText(this, "치지직 채널 ID를 입력하세요", Toast.LENGTH_SHORT).show()
            return
        }
        if (title.isEmpty()) {
            Toast.makeText(this, "방송 제목을 입력하세요", Toast.LENGTH_SHORT).show()
            return
        }

        val tags = tagsStr.split(",").map { it.trim().removePrefix("#") }.filter { it.isNotEmpty() }

        binding.btnApplyLiveSetting.isEnabled = false
        binding.btnApplyLiveSetting.text = "적용 중..."

        chzzkStudioManager.updateLiveTitleAndTags(channelId, title, tags) { result ->
            binding.btnApplyLiveSetting.isEnabled = true
            binding.btnApplyLiveSetting.text = "방송 정보 적용"

            result.onSuccess {
                Toast.makeText(this, "🟢 치지직 방송 정보가 실시간 적용되었습니다!", Toast.LENGTH_SHORT).show()
            }.onFailure { ex ->
                Toast.makeText(this, "방송 정보 적용 실패: ${ex.message}", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun switchCameraMode() {
        if (isCurrentlyStreaming() || isReconnecting) {
            Toast.makeText(this, "방송 송출 또는 재연결 중에는 카메라 모드를 변경할 수 없습니다. 방송을 먼저 종료하세요.", Toast.LENGTH_SHORT).show()
            return
        }

        if (!isUvcMode) {
            // 내장 -> UVC 외장 카메라 전환 시도
            if (uvcCameraManager?.hasExternalCamera() != true) {
                Toast.makeText(
                    this,
                    "⚠️ 연결된 UVC (외장 USB) 카메라를 찾을 수 없습니다.\n스마트폰 USB-C 단자에 웹캠, 액션캠(웹캠 모드), 또는 캡처보드를 C to C 케이블/OTG로 연결해주세요.",
                    Toast.LENGTH_LONG
                ).show()
                return
            }

            isUvcMode = true
            streamingManager?.release()
            binding.btnToggleMode.text = "UVC 카메라"
            binding.btnToggleMode.setBackgroundColor(Color.parseColor("#1565C0"))
            binding.btnFlipCamera.isEnabled = false
            binding.btnFlipCamera.alpha = 0.4f

            binding.surfaceView.postDelayed({
                if (isSurfaceReady && isUvcMode) {
                    val success = uvcCameraManager?.startPreview() == true
                    if (!success) {
                        Toast.makeText(this, "UVC 카메라 프리뷰 시작 실패 (해상도 지원 여부 확인 필요)", Toast.LENGTH_SHORT).show()
                        isUvcMode = false
                        binding.btnToggleMode.text = "내장 카메라"
                        binding.btnToggleMode.setBackgroundColor(Color.parseColor("#455A64"))
                        binding.btnFlipCamera.isEnabled = true
                        binding.btnFlipCamera.alpha = 1.0f
                        streamingManager?.initMode(false)
                        streamingManager?.startPreview()
                    }
                }
            }, 300)
        } else {
            // UVC -> 내장 카메라 복귀
            isUvcMode = false
            uvcCameraManager?.release()
            binding.btnToggleMode.text = "내장 카메라"
            binding.btnToggleMode.setBackgroundColor(Color.parseColor("#455A64"))
            binding.btnFlipCamera.isEnabled = true
            binding.btnFlipCamera.alpha = 1.0f

            binding.surfaceView.postDelayed({
                if (isSurfaceReady && !isUvcMode) {
                    streamingManager?.initMode(false)
                    streamingManager?.startPreview()
                }
            }, 300)
        }
        binding.btnStream.text = "방송 시작"
        binding.btnStream.setBackgroundColor(Color.parseColor("#D32F2F"))
    }

    private fun cancelAutoReconnect() {
        isReconnecting = false
        reconnectAttempts = 0
        reconnectRunnable?.let { reconnectHandler.removeCallbacks(it) }
        reconnectRunnable = null
    }

    private fun scheduleAutoReconnect(reasonNotice: String) {
        if (isUserInitiatedStop) return
        if (reconnectAttempts >= maxReconnectAttempts) {
            cancelAutoReconnect()
            isUserInitiatedStop = true
            StreamingForegroundService.stop(this)
            Toast.makeText(this, "⚠️ 자동 재연결 실패: OBS 소스 활성화 상태 및 설정을 확인하세요.", Toast.LENGTH_LONG).show()
            binding.btnStream.text = "방송 시작"
            binding.btnStream.setBackgroundColor(Color.parseColor("#D32F2F"))
            return
        }

        isReconnecting = true
        reconnectAttempts++
        binding.btnStream.text = "재연결 중... ($reconnectAttempts/$maxReconnectAttempts)"
        binding.btnStream.setBackgroundColor(Color.parseColor("#E65100")) // Deep Orange

        reconnectRunnable?.let { reconnectHandler.removeCallbacks(it) }
        reconnectRunnable = Runnable {
            if (!isUserInitiatedStop && isReconnecting) {
                val url = binding.etServerIp.text.toString().trim()
                val streamId = binding.etStreamId.text.toString().trim()
                val pass = binding.etPassphrase.text.toString().trim()
                if (url.isNotEmpty() && url.startsWith("srt://")) {
                    Log.d("MainActivity", "자동 재연결 시도 ($reconnectAttempts/$maxReconnectAttempts) - $url")
                    if (isUvcMode) uvcCameraManager?.startStream(url, streamId, pass)
                    else streamingManager?.startStream(url, streamId, pass)
                } else {
                    cancelAutoReconnect()
                    binding.btnStream.text = "방송 시작"
                    binding.btnStream.setBackgroundColor(Color.parseColor("#D32F2F"))
                }
            }
        }
        reconnectHandler.postDelayed(reconnectRunnable!!, 2000L)
    }

    private fun toggleStreaming() {
        if (isReconnecting) {
            // 사용자가 재연결 진행 중 중단/취소 클릭
            isUserInitiatedStop = true
            cancelAutoReconnect()
            stopCurrentStream()
            StreamingForegroundService.stop(this)
            binding.btnStream.text = "방송 시작"
            binding.btnStream.setBackgroundColor(Color.parseColor("#D32F2F"))
            Toast.makeText(this, "재연결이 취소되었습니다.", Toast.LENGTH_SHORT).show()
            return
        }

        if (isCurrentlyStreaming()) {
            isUserInitiatedStop = true
            cancelAutoReconnect()
            stopCurrentStream()
            StreamingForegroundService.stop(this)
            binding.btnStream.text = "방송 시작"
            binding.btnStream.setBackgroundColor(Color.parseColor("#D32F2F"))
        } else {
            val url = binding.etServerIp.text.toString().trim()
            val streamId = binding.etStreamId.text.toString().trim()
            val pass = binding.etPassphrase.text.toString().trim()
            if (url.isEmpty() || !url.startsWith("srt://")) {
                Toast.makeText(this, "올바른 SRT 주소를 입력하세요", Toast.LENGTH_SHORT).show()
                return
            }
            isUserInitiatedStop = false
            cancelAutoReconnect()
            StreamingForegroundService.start(this)
            binding.btnStream.text = "연결 중..."
            binding.btnStream.setBackgroundColor(Color.parseColor("#1565C0"))
            if (isUvcMode) uvcCameraManager?.startStream(url, streamId, pass)
            else streamingManager?.startStream(url, streamId, pass)
        }
    }

    private fun isCurrentlyStreaming() =
        if (isUvcMode) uvcCameraManager?.isStreaming == true
        else streamingManager?.isStreaming() == true

    private fun stopCurrentStream() {
        if (isUvcMode) uvcCameraManager?.stopStream()
        else streamingManager?.stopStream()
    }

    // ── ConnectChecker 콜백 ──
    override fun onConnectionSuccess() {
        runOnUiThread {
            val wasReconnecting = isReconnecting
            cancelAutoReconnect()
            isUserInitiatedStop = false
            if (wasReconnecting) {
                Toast.makeText(this, "🟢 SRT 방송이 자동으로 재연결되었습니다!", Toast.LENGTH_SHORT).show()
            } else {
                Toast.makeText(this, "🔴 SRT 송출 시작!", Toast.LENGTH_SHORT).show()
            }
            binding.btnStream.text = "방송 종료"
            binding.btnStream.setBackgroundColor(Color.parseColor("#388E3C"))
        }
    }

    override fun onConnectionFailed(reason: String) {
        runOnUiThread {
            // 이미 재연결 루프 중이거나, 최소 1회 이상 재연결 시도 중인 경우에만 계속 재시도
            // 첫 번째 연결 시도에서 실패하면(isReconnecting=false, reconnectAttempts=0) 즉시 에러 표시
            val shouldAutoReconnect = !isUserInitiatedStop && isReconnecting && reconnectAttempts < maxReconnectAttempts
            if (shouldAutoReconnect) {
                scheduleAutoReconnect(reason)
            } else {
                cancelAutoReconnect()
                isUserInitiatedStop = true
                StreamingForegroundService.stop(this)
                val msg = if (reason.contains("Poll timed out", ignoreCase = true)) {
                    "⚠️ OBS 수신 대기 타임아웃\n(OBS SRT 미디어 소스가 재연결 준비 중입니다. 5~10초 후 다시 [방송 시작]을 누르거나 OBS에서 소스 눈 아이콘을 껐다 켜주세요)"
                } else {
                    "송출 실패: $reason"
                }
                Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                binding.btnStream.text = "방송 시작"
                binding.btnStream.setBackgroundColor(Color.parseColor("#D32F2F"))
            }
        }
    }

    override fun onDisconnect() {
        runOnUiThread {
            if (!isUserInitiatedStop && (isReconnecting || reconnectAttempts < maxReconnectAttempts)) {
                scheduleAutoReconnect("연결 해제됨")
            } else {
                cancelAutoReconnect()
                isUserInitiatedStop = true
                StreamingForegroundService.stop(this)
                Toast.makeText(this, "방송 종료됨", Toast.LENGTH_SHORT).show()
                binding.btnStream.text = "방송 시작"
                binding.btnStream.setBackgroundColor(Color.parseColor("#D32F2F"))
            }
        }
    }

    private fun checkPermissions(): Boolean {
        val camera = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
        val audio = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
        return camera && audio
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == PERMISSION_REQ_CODE) {
            val camera = ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
            val audio = ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED
            if (camera && audio) {
                setupApp()
            } else {
                Toast.makeText(this, "카메라 및 마이크 권한이 필수입니다.", Toast.LENGTH_LONG).show()
                finish()
            }
        }
    }

    override fun dispatchKeyEvent(event: android.view.KeyEvent): Boolean {
        if (touchLockManager.isLocked) {
            // 볼륨 버튼은 방송 중 모니터링 볼륨 조절을 위해 허용, 그 외(BACK, HOME, MENU 등)는 완전 차단
            if (event.keyCode == android.view.KeyEvent.KEYCODE_VOLUME_UP || 
                event.keyCode == android.view.KeyEvent.KEYCODE_VOLUME_DOWN) {
                return super.dispatchKeyEvent(event)
            }
            return true // 모든 시스템 키 이벤트 소비 (무반응 처리)
        }
        return super.dispatchKeyEvent(event)
    }

    private fun startCameraPreview() {
        if (!isSurfaceReady) return
        if (!isUvcMode) {
            streamingManager?.startPreview()
        } else {
            uvcCameraManager?.startPreview()
        }
    }

    override fun onResume() {
        super.onResume()
        // 송출 중에는 프리뷰 재시작 불필요 (스트리밍이 이미 카메라를 점유 중)
        if (checkPermissions() && isSurfaceReady && !isCurrentlyStreaming() && !isReconnecting) {
            startCameraPreview()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        try {
            unregisterReceiver(usbReceiver)
        } catch (e: Exception) {
            // ignore if not registered
        }
        cancelAutoReconnect()
        reconnectHandler.removeCallbacksAndMessages(null)
        stopProgramMonitor()
        stopChzzkTimer()
        chzzkTimerHandler.removeCallbacksAndMessages(null)
        streamingManager?.release()
        uvcCameraManager?.release()
        audioRoutingManager.release()
        touchLockManager.release()
        obsWebSocketManager?.release()
        chzzkChatManager?.release()
        ttsManager.release()
    }
}
