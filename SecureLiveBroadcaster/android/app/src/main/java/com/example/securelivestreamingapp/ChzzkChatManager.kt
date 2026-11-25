package com.example.securelivestreamingapp

import android.os.Handler
import android.os.Looper
import android.util.Log
import org.java_websocket.client.WebSocketClient
import org.java_websocket.handshake.ServerHandshake
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.net.URLEncoder
import kotlin.concurrent.thread

class ChzzkChatManager(
    private val rawChannelInput: String,
    private val listener: ChatListener
) {
    interface ChatListener {
        fun onChatMessage(userName: String, message: String)
        fun onConnected()
        fun onDisconnected()
        fun onError(msg: String)
    }

    private var wsClient: WebSocketClient? = null
    var isConnected = false
        private set

    private val mainHandler = Handler(Looper.getMainLooper())
    private var pingRunnable: Runnable? = null
    private var actualChannelId: String = ""
    private var chatChannelId: String = ""
    private var accessToken: String = ""
    private var extraToken: String = ""
    private var sid: String = ""

    private var connectionStartTime: Long = 0L

    fun connect() {
        val input = rawChannelInput.trim()
        if (input.isEmpty()) {
            listener.onError("치지직 채널 ID 또는 이름을 입력하세요")
            return
        }

        connectionStartTime = System.currentTimeMillis()
        Log.d("ChzzkChat", "채팅 연결 프로세스 시작: '$input' (기준 시각: $connectionStartTime)")

        disconnect()

        thread {
            try {
                // 1. 입력값(URL, 해시값, 닉네임) 분석 및 실제 32자리 치지직 channelId 획득
                actualChannelId = resolveChannelId(input)
                if (actualChannelId.isEmpty()) {
                    mainHandler.post { listener.onError("치지직 채널을 찾을 수 없습니다: $input") }
                    return@thread
                }
                Log.d("ChzzkChat", "확정된 채널 ID: $actualChannelId")

                // 2. live-status / live-detail 조회를 통해 실제 chatChannelId 획득
                val (channelChatId, isAdultBroadcast) = fetchChatChannelIdWithStatus(actualChannelId)
                chatChannelId = channelChatId
                if (chatChannelId.isEmpty()) {
                    val errorMsg = if (isAdultBroadcast) {
                        "⚠️ 현재 방송이 '연령 제한(19금)'으로 설정되어 채팅방이 비공개 상태입니다.\n치지직 스튜디오에서 [연령 제한]을 해제하시면 즉시 연결됩니다!"
                    } else {
                        "채팅방 정보를 찾을 수 없습니다.\n(채널 ID를 확인하시거나 치지직 방송이 켜져 있는지 확인하세요)"
                    }
                    mainHandler.post { listener.onError(errorMsg) }
                    return@thread
                }
                Log.d("ChzzkChat", "대상 chatChannelId: $chatChannelId")

                // 3. 채팅 access-token 및 extraToken 발급
                val tokenPair = fetchAccessToken(chatChannelId)
                accessToken = tokenPair.first
                extraToken = tokenPair.second
                Log.d("ChzzkChat", "access-token 획득 여부: ${accessToken.isNotEmpty()}, extraToken: ${extraToken.isNotEmpty()}")

                // 4. WebSocket 연결 (kr-ss1 ~ kr-ss5 중 kr-ss1)
                val wsUrl = "wss://kr-ss1.chat.naver.com/chat"
                val uri = URI(wsUrl)
                wsClient = object : WebSocketClient(uri) {
                    override fun onOpen(handshake: ServerHandshake?) {
                        Log.d("ChzzkChat", "치지직 WebSocket 소켓 연결 성공, cmd 100(CONNECT) 전송")
                        sendConnectMessage()
                    }

                    override fun onMessage(message: String?) {
                        message ?: return
                        parseChatMessage(message)
                    }

                    override fun onClose(code: Int, reason: String?, remote: Boolean) {
                        val wasConnected = isConnected
                        isConnected = false
                        stopPing()
                        Log.d("ChzzkChat", "치지직 채팅 소켓 종료: $reason ($code, wasConnected=$wasConnected)")
                        if (wasConnected) {
                            mainHandler.post { listener.onDisconnected() }
                        }
                    }

                    override fun onError(ex: Exception?) {
                        Log.e("ChzzkChat", "치지직 채팅 소켓 에러", ex)
                        mainHandler.post { listener.onError("채팅 소켓 오류: ${ex?.message}") }
                    }
                }
                wsClient?.connect()
            } catch (e: Exception) {
                Log.e("ChzzkChat", "치지직 연결 준비 중 에러", e)
                mainHandler.post { listener.onError("연결 준비 실패: ${e.message}") }
            }
        }
    }

    /**
     * 사용자가 URL, 32자리 채널ID, 또는 채널 이름을 입력했을 때 이를 정규 32자리 채널ID로 변환
     */
    private fun resolveChannelId(input: String): String {
        var clean = input.trimEnd('/')
        // 1) 전체 URL 파싱: https://chzzk.naver.com/live/{id} 또는 https://chzzk.naver.com/{id}
        if (clean.contains("chzzk.naver.com")) {
            clean = clean.substringAfterLast("/").substringBefore("?")
        } else if (clean.contains("/")) {
            clean = clean.substringAfterLast("/").substringBefore("?")
        }

        // 2) 32자리 영문/숫자 16진수 채널 ID인지 검증
        if (clean.matches(Regex("^[a-fA-F0-9]{32}$"))) {
            return clean
        }

        // 3) 만약 32자리 해시가 아니라면 채널 닉네임으로 간주하고 치지직 검색 API로 채널 ID 자동 탐색
        Log.d("ChzzkChat", "'$clean'을 채널 닉네임으로 검색 시도")
        return try {
            val encoded = URLEncoder.encode(clean, "UTF-8")
            val url = URL("https://api.chzzk.naver.com/service/v1/search/channels?keyword=$encoded&size=1")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("User-Agent", "Mozilla/5.0")
                connectTimeout = 5000
                readTimeout = 5000
            }
            if (conn.responseCode == 200) {
                val reader = BufferedReader(InputStreamReader(conn.inputStream))
                val resp = reader.readText()
                reader.close()
                val json = JSONObject(resp)
                val content = json.optJSONObject("content")
                val dataArr = content?.optJSONArray("data")
                if (dataArr != null && dataArr.length() > 0) {
                    val firstItem = dataArr.getJSONObject(0)
                    val channelObj = firstItem.optJSONObject("channel")
                    val foundId = getSafeString(channelObj, "channelId")
                    val foundName = getSafeString(channelObj, "channelName")
                    Log.d("ChzzkChat", "채널 검색 성공: '$foundName' -> $foundId")
                    foundId
                } else ""
            } else ""
        } catch (e: Exception) {
            Log.e("ChzzkChat", "채널 검색 API 실패", e)
            ""
        }
    }

    private fun getSafeString(obj: JSONObject?, key: String): String {
        if (obj == null || obj.isNull(key)) return ""
        val v = obj.optString(key, "")
        return if (v == "null" || v.isEmpty()) "" else v.trim()
    }

    /**
     * live-status -> live-detail 순으로 안전하게 chatChannelId를 획득하고,
     * 만약 연령제한(adult=true)으로 인해 비공개 차단되었는지도 판별합니다.
     */
    private fun fetchChatChannelIdWithStatus(targetChannelId: String): Pair<String, Boolean> {
        // 만약 사용자가 이미 6자리 내외의 chatChannelId를 직접 입력한 경우 (예: N2kq0z 등)
        if (targetChannelId.matches(Regex("^[a-zA-Z0-9]{4,10}$")) && targetChannelId.length != 32) {
            Log.d("ChzzkChat", "직접 입력된 chatChannelId로 간주: $targetChannelId")
            return Pair(targetChannelId, false)
        }

        var isAdult = false

        // 1차 시도: polling live-status
        try {
            val url = URL("https://api.chzzk.naver.com/polling/v2/channels/$targetChannelId/live-status")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("User-Agent", "Mozilla/5.0")
                val cookie = SecurityManager.getNaverCookie()
                if (cookie.isNotEmpty()) {
                    setRequestProperty("Cookie", cookie)
                }
                connectTimeout = 5000
                readTimeout = 5000
            }
            if (conn.responseCode == 200) {
                val resp = BufferedReader(InputStreamReader(conn.inputStream)).use { it.readText() }
                val json = JSONObject(resp)
                val content = json.optJSONObject("content")
                if (content?.optBoolean("adult", false) == true) {
                    isAdult = true
                }
                val cid = getSafeString(content, "chatChannelId")
                if (cid.isNotEmpty()) {
                    Log.d("ChzzkChat", "live-status에서 chatChannelId 획득: $cid")
                    return Pair(cid, isAdult)
                }
            }
        } catch (e: Exception) {
            Log.w("ChzzkChat", "live-status 조회 실패: ${e.message}")
        }

        // 2차 시도: service live-detail
        try {
            val url = URL("https://api.chzzk.naver.com/service/v2/channels/$targetChannelId/live-detail")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("User-Agent", "Mozilla/5.0")
                val cookie = SecurityManager.getNaverCookie()
                if (cookie.isNotEmpty()) {
                    setRequestProperty("Cookie", cookie)
                }
                connectTimeout = 5000
                readTimeout = 5000
            }
            if (conn.responseCode == 200) {
                val resp = BufferedReader(InputStreamReader(conn.inputStream)).use { it.readText() }
                val json = JSONObject(resp)
                val content = json.optJSONObject("content")
                if (content?.optBoolean("adult", false) == true) {
                    isAdult = true
                }
                val cid = getSafeString(content, "chatChannelId")
                if (cid.isNotEmpty()) {
                    Log.d("ChzzkChat", "live-detail에서 chatChannelId 획득: $cid")
                    return Pair(cid, isAdult)
                }
            }
        } catch (e: Exception) {
            Log.w("ChzzkChat", "live-detail 조회 실패: ${e.message}")
        }

        // 3차 시도: channel 기본 정보
        try {
            val url = URL("https://api.chzzk.naver.com/service/v1/channels/$targetChannelId")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("User-Agent", "Mozilla/5.0")
                val cookie = SecurityManager.getNaverCookie()
                if (cookie.isNotEmpty()) {
                    setRequestProperty("Cookie", cookie)
                }
                connectTimeout = 5000
                readTimeout = 5000
            }
            if (conn.responseCode == 200) {
                val resp = BufferedReader(InputStreamReader(conn.inputStream)).use { it.readText() }
                val json = JSONObject(resp)
                val content = json.optJSONObject("content")
                val cid = getSafeString(content, "chatChannelId")
                if (cid.isNotEmpty()) {
                    Log.d("ChzzkChat", "channels 기본정보에서 chatChannelId 획득: $cid")
                    return Pair(cid, isAdult)
                }
            }
        } catch (e: Exception) {
            Log.w("ChzzkChat", "channels 기본정보 조회 실패: ${e.message}")
        }

        return Pair("", isAdult)
    }

    private fun fetchAccessToken(targetChatChannelId: String): Pair<String, String> {
        return try {
            val url = URL("https://comm-api.game.naver.com/nng_main/v1/chats/access-token?channelId=$targetChatChannelId&chatType=STREAMING")
            val conn = (url.openConnection() as HttpURLConnection).apply {
                requestMethod = "GET"
                setRequestProperty("User-Agent", "Mozilla/5.0")
                val cookie = SecurityManager.getNaverCookie()
                if (cookie.isNotEmpty()) {
                    setRequestProperty("Cookie", cookie)
                }
                connectTimeout = 5000
                readTimeout = 5000
            }
            if (conn.responseCode == 200) {
                val resp = BufferedReader(InputStreamReader(conn.inputStream)).use { it.readText() }
                val json = JSONObject(resp)
                val content = json.optJSONObject("content")
                val token = getSafeString(content, "accessToken")
                val extra = getSafeString(content, "extraToken")
                Pair(token, extra)
            } else Pair("", "")
        } catch (e: Exception) {
            Log.e("ChzzkChat", "access-token 요청 실패", e)
            Pair("", "")
        }
    }

    private fun sendConnectMessage() {
        val bdy = JSONObject().apply {
            put("uid", JSONObject.NULL)
            put("devType", 2001)
            put("auth", "READ")
            if (accessToken.isNotEmpty()) {
                put("accTkn", accessToken)
            }
            if (extraToken.isNotEmpty()) {
                put("extraToken", extraToken)
            }
        }
        val connectMsg = JSONObject().apply {
            put("ver", "3")
            put("svcid", "game")
            put("cid", chatChannelId)
            put("cmd", 100)
            put("tid", 1)
            put("bdy", bdy)
        }
        Log.d("ChzzkChat", "CONNECT 메시지 송신 (cid: $chatChannelId)")
        wsClient?.send(connectMsg.toString())
    }

    private fun sendPong() {
        val pong = JSONObject().apply {
            put("ver", "3")
            put("cmd", 10000)
        }
        try {
            wsClient?.send(pong.toString())
            Log.d("ChzzkChat", "PONG(cmd 10000) 응답 전송")
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun parseChatMessage(raw: String) {
        try {
            val json = JSONObject(raw)
            val cmd = json.optInt("cmd", 0)

            when (cmd) {
                0 -> { // 서버 PING 수신 -> 즉시 PONG 응답
                    Log.d("ChzzkChat", "서버 PING(cmd 0) 수신 -> PONG 전송")
                    sendPong()
                }
                10100 -> { // cmd 100 응답 (연결 결과)
                    val retCode = json.optInt("retCode", -1)
                    val retMsg = json.optString("retMsg", "")
                    val bdy = json.optJSONObject("bdy")
                    sid = getSafeString(bdy, "sid")

                    // ★ 중요: retCode == 0 이거나 SUCCESS일 때만 진짜 연결 성공!
                    if (retCode == 0 || retMsg.equals("SUCCESS", ignoreCase = true)) {
                        isConnected = true
                        Log.d("ChzzkChat", "🟢 치지직 연결 정상 승인 완료! (sid: $sid), 실시간 채팅 청취 시작")
                        startPing()
                        mainHandler.post { listener.onConnected() }
                    } else {
                        isConnected = false
                        val errorDesc = if (retMsg.isNotEmpty()) retMsg else "오류코드 $retCode"
                        Log.e("ChzzkChat", "🔴 치지직 연결 거절 응답: retCode=$retCode, retMsg=$retMsg")
                        mainHandler.post { listener.onError("채팅 서버 연결 실패: $errorDesc") }
                        disconnect()
                    }
                }
                15101 -> {
                    // 최근 채팅 기록 (실시간 채팅만 필요하므로 무시)
                }
                93101, 93102 -> { // 93101: 실시간 채팅, 93102: 후원/알림
                    val messageList = when {
                        json.optJSONArray("bdy") != null -> json.getJSONArray("bdy")
                        json.optJSONObject("bdy") != null -> {
                            val bdy = json.getJSONObject("bdy")
                            bdy.optJSONArray("messageList") ?: JSONArray()
                        }
                        else -> JSONArray()
                    }
                    Log.d("ChzzkChat", "실시간 채팅 수신 (cmd: $cmd, 메시지 수: ${messageList.length()})")
                    for (i in 0 until messageList.length()) {
                        val msg = messageList.getJSONObject(i)
                        
                        val msgTime = when {
                            msg.has("msgTime") -> msg.optLong("msgTime", 0L)
                            msg.has("ctime") -> msg.optLong("ctime", 0L)
                            else -> 0L
                        }

                        val content = when {
                            msg.has("msg") && !msg.isNull("msg") -> msg.optString("msg")
                            msg.has("content") && !msg.isNull("content") -> msg.optString("content")
                            msg.has("message") && !msg.isNull("message") -> msg.optString("message")
                            else -> ""
                        }.trim()

                        val profileObj = msg.optJSONObject("profile") ?: runCatching {
                            val pStr = msg.optString("profile", "{}")
                            JSONObject(pStr)
                        }.getOrDefault(JSONObject())

                        val userName = when {
                            profileObj.has("nickname") -> profileObj.optString("nickname")
                            profileObj.has("name") -> profileObj.optString("name")
                            msg.has("nickname") -> msg.optString("nickname")
                            else -> "시청자"
                        }.trim().ifEmpty { "시청자" }

                        // 연결 시점 이전에 작성된 과거 메시지는 필터링
                        if (msgTime > 0 && msgTime < connectionStartTime) {
                            continue
                        }

                        if (content.isNotEmpty()) {
                            Log.d("ChzzkChat", "=> [실시간 채팅 발송] $userName: $content")
                            mainHandler.post { listener.onChatMessage(userName, content) }
                        }
                    }
                }
                10000 -> {
                    // PONG 응답 수신
                }
                else -> {
                    Log.d("ChzzkChat", "기타 cmd 수신: $cmd")
                }
            }
        } catch (e: Exception) {
            Log.e("ChzzkChat", "채팅 메시지 파싱 에러", e)
        }
    }

    private fun startPing() {
        stopPing()
        pingRunnable = object : Runnable {
            override fun run() {
                try {
                    if (wsClient?.isOpen == true) {
                        val ping = JSONObject().apply {
                            put("ver", "3")
                            put("cmd", 0)
                        }
                        wsClient?.send(ping.toString())
                        mainHandler.postDelayed(this, 20000)
                    }
                } catch (e: Exception) {
                    e.printStackTrace()
                }
            }
        }
        mainHandler.postDelayed(pingRunnable!!, 20000)
    }

    private fun stopPing() {
        pingRunnable?.let { mainHandler.removeCallbacks(it) }
        pingRunnable = null
    }

    fun disconnect() {
        stopPing()
        try {
            wsClient?.close()
        } catch (e: Exception) {
            e.printStackTrace()
        } finally {
            wsClient = null
            isConnected = false
            sid = ""
        }
    }

    fun release() {
        disconnect()
    }
}
