package com.example.securelivestreamingapp

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.util.Base64
import android.util.Log
import org.java_websocket.client.WebSocketClient
import org.java_websocket.handshake.ServerHandshake
import org.json.JSONObject
import java.net.URI
import java.security.MessageDigest
import java.util.UUID

data class ObsSceneItem(
    val id: Int,
    val name: String,
    var enabled: Boolean
)

class ObsWebSocketManager(
    private val host: String,
    private val port: Int = 4455,
    private val password: String = "",
    private val listener: ObsListener
) {
    interface ObsListener {
        fun onConnected()
        fun onDisconnected()
        fun onStreamingStarted()
        fun onStreamingStopped()
        fun onError(msg: String)
        fun onStreamingStatus(isStreaming: Boolean, timecode: String? = null, durationMs: Long? = null)
        fun onSceneListReceived(currentScene: String, scenes: List<String>) {}
        fun onSceneItemListReceived(sceneName: String, items: List<ObsSceneItem>) {}
        fun onCurrentSceneChanged(sceneName: String) {}
        fun onSceneItemStateChanged(sceneName: String, itemId: Int, enabled: Boolean) {}
        fun onScreenshotReceived(bitmap: Bitmap) {}
    }

    private var wsClient: WebSocketClient? = null
    var isConnected = false
        private set

    private var lastRequestedSceneForItems = ""
    private var reusableBitmap: Bitmap? = null
    private val isDecodingScreenshot = java.util.concurrent.atomic.AtomicBoolean(false)

    fun connect() {
        try {
            disconnect()
            val uri = URI("ws://$host:$port")
            wsClient = object : WebSocketClient(uri) {
                override fun onOpen(handshake: ServerHandshake?) {
                    Log.d("OBSWebSocket", "OBS WebSocket 소켓 열림 (인증 대기)")
                }

                override fun onMessage(message: String?) {
                    message ?: return
                    handleMessage(message)
                }

                override fun onClose(code: Int, reason: String?, remote: Boolean) {
                    isConnected = false
                    Log.d("OBSWebSocket", "OBS 연결 종료: $reason ($code)")
                    listener.onDisconnected()
                }

                override fun onError(ex: Exception?) {
                    Log.e("OBSWebSocket", "OBS 소켓 에러", ex)
                    listener.onError("OBS 오류: ${ex?.message}")
                }
            }
            wsClient?.connect()
        } catch (e: Exception) {
            listener.onError("연결 실패: ${e.message}")
        }
    }

    private fun handleMessage(raw: String) {
        try {
            val json = JSONObject(raw)
            val op = json.optInt("op", -1)
            val data = json.optJSONObject("d") ?: JSONObject()

            when (op) {
                0 -> { // Hello
                    val authObj = data.optJSONObject("authentication")
                    val challenge = authObj?.optString("challenge", "") ?: ""
                    val salt = authObj?.optString("salt", "") ?: ""
                    identify(challenge, salt)
                }
                2 -> { // Identified (인증 성공)
                    isConnected = true
                    Log.d("OBSWebSocket", "OBS 인증 성공 및 연결 완료")
                    listener.onConnected()
                    getStreamingStatus()
                    getSceneList()
                }
                5 -> { // Event (실시간 이벤트 구독 수신)
                    val eventType = data.optString("eventType", "")
                    val eventData = data.optJSONObject("eventData") ?: JSONObject()
                    when (eventType) {
                        "StreamStateChanged" -> {
                            val active = eventData.optBoolean("outputActive", false)
                            val state = eventData.optString("outputState", "")
                            if (active || state == "OBS_WEBSOCKET_OUTPUT_STARTED") {
                                listener.onStreamingStarted()
                            } else if (!active || state == "OBS_WEBSOCKET_OUTPUT_STOPPED") {
                                listener.onStreamingStopped()
                            }
                            listener.onStreamingStatus(active)
                        }
                        "CurrentProgramSceneChanged" -> {
                            val scName = eventData.optString("sceneName", "")
                            if (scName.isNotEmpty()) {
                                listener.onCurrentSceneChanged(scName)
                                getSceneItemList(scName)
                            }
                        }
                        "SceneItemEnableStateChanged" -> {
                            val scName = eventData.optString("sceneName", "")
                            val itemId = eventData.optInt("sceneItemId", -1)
                            val enabled = eventData.optBoolean("sceneItemEnabled", true)
                            if (scName.isNotEmpty() && itemId != -1) {
                                listener.onSceneItemStateChanged(scName, itemId, enabled)
                            }
                        }
                        "SceneListChanged" -> {
                            getSceneList()
                        }
                    }
                }
                7 -> { // RequestResponse
                    val reqType = data.optString("requestType", "")
                    val status = data.optJSONObject("requestStatus")
                    val success = status?.optBoolean("result", false) ?: false
                    val comment = status?.optString("comment", "") ?: ""
                    if (!success && comment.isNotEmpty() && reqType != "GetSourceScreenshot") {
                        listener.onError("OBS 요청 실패 ($reqType): $comment")
                    }
                    when (reqType) {
                        "StartStream" -> if (success) listener.onStreamingStarted()
                        "StopStream" -> if (success) listener.onStreamingStopped()
                        "GetStreamStatus" -> {
                            val rd = data.optJSONObject("responseData")
                            val active = rd?.optBoolean("outputActive", false) ?: false
                            val timecode = rd?.optString("outputTimecode", "")
                            val durationMs = rd?.optLong("outputDuration", 0L)
                            listener.onStreamingStatus(active, if (timecode?.isNotEmpty() == true) timecode else null, durationMs)
                            if (active) {
                                listener.onStreamingStarted()
                            }
                        }
                        "GetSceneList" -> {
                            if (success) {
                                val rd = data.optJSONObject("responseData")
                                val currentScene = rd?.optString("currentProgramSceneName", "") ?: ""
                                val scenesArr = rd?.optJSONArray("scenes")
                                val list = mutableListOf<String>()
                                if (scenesArr != null) {
                                    for (i in 0 until scenesArr.length()) {
                                        val scObj = scenesArr.optJSONObject(i)
                                        val name = scObj?.optString("sceneName", "") ?: ""
                                        if (name.isNotEmpty()) list.add(name)
                                    }
                                }
                                listener.onSceneListReceived(currentScene, list)
                                if (currentScene.isNotEmpty()) {
                                    getSceneItemList(currentScene)
                                }
                            }
                        }
                        "GetSceneItemList" -> {
                            if (success) {
                                val rd = data.optJSONObject("responseData")
                                val itemsArr = rd?.optJSONArray("sceneItems")
                                val items = mutableListOf<ObsSceneItem>()
                                if (itemsArr != null) {
                                    for (i in 0 until itemsArr.length()) {
                                        val itObj = itemsArr.optJSONObject(i) ?: continue
                                        val id = itObj.optInt("sceneItemId", -1)
                                        val name = itObj.optString("sourceName", "")
                                        val enabled = itObj.optBoolean("sceneItemEnabled", true)
                                        if (id != -1 && name.isNotEmpty()) {
                                            items.add(ObsSceneItem(id, name, enabled))
                                        }
                                    }
                                }
                                listener.onSceneItemListReceived(lastRequestedSceneForItems, items)
                            }
                        }
                        "GetSourceScreenshot" -> {
                            if (success) {
                                val rd = data.optJSONObject("responseData")
                                val imgStr = rd?.optString("imageData", "") ?: ""
                                if (imgStr.isNotEmpty()) {
                                    if (isDecodingScreenshot.compareAndSet(false, true)) {
                                        try {
                                            val commaIdx = imgStr.indexOf(',')
                                            val base64Data = if (commaIdx >= 0) imgStr.substring(commaIdx + 1) else imgStr
                                            val bytes = Base64.decode(base64Data, Base64.DEFAULT)
                                            val opts = BitmapFactory.Options().apply {
                                                inPreferredConfig = Bitmap.Config.RGB_565
                                                inMutable = true
                                                if (reusableBitmap != null && !reusableBitmap!!.isRecycled && reusableBitmap!!.isMutable) {
                                                    inBitmap = reusableBitmap
                                                }
                                            }
                                            val bitmap = try {
                                                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
                                            } catch (e: IllegalArgumentException) {
                                                opts.inBitmap = null
                                                BitmapFactory.decodeByteArray(bytes, 0, bytes.size, opts)
                                            }
                                            if (bitmap != null) {
                                                reusableBitmap = bitmap
                                                listener.onScreenshotReceived(bitmap)
                                            }
                                        } catch (oom: OutOfMemoryError) {
                                            Log.e("OBSWebSocket", "스크린샷 OOM 방어 및 메모리 해제", oom)
                                            try {
                                                reusableBitmap?.recycle()
                                                reusableBitmap = null
                                            } catch (t: Throwable) {}
                                            System.gc()
                                        } catch (e: Exception) {
                                            Log.e("OBSWebSocket", "스크린샷 디코딩 에러: ${e.message}")
                                        } finally {
                                            isDecodingScreenshot.set(false)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        } catch (e: Exception) {
            Log.e("OBSWebSocket", "파싱 오류: ${e.message}")
        }
    }

    private fun identify(challenge: String, salt: String) {
        val authString = if (password.isNotEmpty() && challenge.isNotEmpty()) {
            val secret = sha256Base64(password + salt)
            sha256Base64(secret + challenge)
        } else ""

        val msg = JSONObject().apply {
            put("op", 1) // Identify
            put("d", JSONObject().apply {
                put("rpcVersion", 1)
                if (authString.isNotEmpty()) {
                    put("authentication", authString)
                }
                // 실시간 장면/소스/스트림 상태 이벤트 구독 (1023 = All Events)
                put("eventSubscriptions", 1023)
            })
        }
        wsClient?.send(msg.toString())
    }

    fun startStreaming() = sendRequest("StartStream")
    fun stopStreaming() = sendRequest("StopStream")
    fun getStreamingStatus() = sendRequest("GetStreamStatus")

    fun getSceneList() = sendRequest("GetSceneList")

    fun setCurrentProgramScene(sceneName: String) {
        sendRequest("SetCurrentProgramScene", JSONObject().apply {
            put("sceneName", sceneName)
        })
    }

    fun getSceneItemList(sceneName: String) {
        lastRequestedSceneForItems = sceneName
        sendRequest("GetSceneItemList", JSONObject().apply {
            put("sceneName", sceneName)
        })
    }

    fun setSceneItemEnabled(sceneName: String, itemId: Int, enabled: Boolean) {
        sendRequest("SetSceneItemEnabled", JSONObject().apply {
            put("sceneName", sceneName)
            put("sceneItemId", itemId)
            put("sceneItemEnabled", enabled)
        })
    }

    /**
     * OBS 최종 화면(Program Scene)의 초경량 스냅샷(240x135 JPEG, 35% 품질) 요청 (~2.5KB)
     * 192x108 PiP 카드에 최적화하여 힙 메모리 및 WebSocket 부하 75% 절감
     */
    fun requestProgramScreenshot(sourceOrSceneName: String) {
        sendRequest("GetSourceScreenshot", JSONObject().apply {
            put("sourceName", sourceOrSceneName)
            put("imageFormat", "jpeg")
            put("imageWidth", 240)
            put("imageHeight", 135)
            put("imageCompressionQuality", 35)
        })
    }

    private fun sendRequest(requestType: String, requestData: JSONObject = JSONObject()) {
        if (!isConnected) return
        try {
            val msg = JSONObject().apply {
                put("op", 6) // Request
                put("d", JSONObject().apply {
                    put("requestType", requestType)
                    put("requestId", UUID.randomUUID().toString())
                    put("requestData", requestData)
                })
            }
            wsClient?.send(msg.toString())
        } catch (e: Exception) {
            Log.e("OBSWebSocket", "sendRequest error ($requestType): ${e.message}")
        }
    }

    private fun sha256Base64(input: String): String {
        val digest = MessageDigest.getInstance("SHA-256")
        val hash = digest.digest(input.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(hash, Base64.NO_WRAP)
    }

    fun disconnect() {
        try {
            wsClient?.close()
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            wsClient = null
            isConnected = false
        }
    }

    fun release() {
        disconnect()
        try {
            reusableBitmap?.recycle()
            reusableBitmap = null
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }
}
