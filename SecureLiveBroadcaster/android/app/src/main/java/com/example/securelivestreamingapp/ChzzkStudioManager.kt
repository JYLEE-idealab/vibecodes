package com.example.securelivestreamingapp

import android.os.Handler
import android.os.Looper
import android.util.Log
import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.HttpURLConnection
import java.net.URL
import kotlin.concurrent.thread

data class LiveSetting(
    val title: String,
    val categoryType: String,
    val categoryId: String,
    val categoryName: String,
    val tags: List<String>,
    val adult: Boolean,
    val rawContent: JSONObject
)

class ChzzkStudioManager(private val context: android.content.Context) {

    private val mainHandler = Handler(Looper.getMainLooper())
    private var lastRawContent: JSONObject? = null

    /**
     * 현재 치지직 스튜디오의 방송 설정(제목, 카테고리, 태그 등) 조회
     */
    fun fetchLiveSetting(channelId: String, callback: (Result<LiveSetting>) -> Unit) {
        val cookie = SecurityManager.getNaverCookie()
        if (cookie.isEmpty()) {
            callback(Result.failure(Exception("네이버 로그인이 필요합니다.")))
            return
        }

        thread {
            try {
                val url = URL("https://api.chzzk.naver.com/manage/v1/channels/$channelId/live-setting/normal")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                    setRequestProperty("Cookie", cookie)
                    setRequestProperty("Referer", "https://studio.chzzk.naver.com/")
                    connectTimeout = 5000
                    readTimeout = 5000
                }

                val responseCode = conn.responseCode
                if (responseCode == 200) {
                    val resp = BufferedReader(InputStreamReader(conn.inputStream)).use { it.readText() }
                    val json = JSONObject(resp)
                    val content = json.optJSONObject("content")
                    if (content != null) {
                        lastRawContent = content
                        val title = content.optString("defaultLiveTitle", "")
                        val categoryObj = content.optJSONObject("category")
                        val categoryType = categoryObj?.optString("categoryType", "") ?: ""
                        val categoryId = categoryObj?.optString("categoryId", "") ?: ""
                        val categoryName = categoryObj?.optString("categoryValue", "") ?: ""
                        val adult = content.optBoolean("adult", false)

                        val tagsList = mutableListOf<String>()
                        val tagsArr = content.optJSONArray("tags")
                        if (tagsArr != null) {
                            for (i in 0 until tagsArr.length()) {
                                val t = tagsArr.optString(i, "")
                                if (t.isNotEmpty()) tagsList.add(t)
                            }
                        }

                        val setting = LiveSetting(
                            title = title,
                            categoryType = categoryType,
                            categoryId = categoryId,
                            categoryName = categoryName,
                            tags = tagsList,
                            adult = adult,
                            rawContent = content
                        )
                        mainHandler.post { callback(Result.success(setting)) }
                    } else {
                        mainHandler.post { callback(Result.failure(Exception("방송 정보 내용이 비어있습니다."))) }
                    }
                } else if (responseCode == 401 || responseCode == 403) {
                    mainHandler.post { callback(Result.failure(Exception("네이버 로그인 세션이 만료되었습니다. 다시 로그인해주세요."))) }
                } else {
                    mainHandler.post { callback(Result.failure(Exception("방송 설정 조회 실패 (HTTP $responseCode)"))) }
                }
            } catch (e: Exception) {
                Log.e("ChzzkStudio", "방송 설정 조회 오류", e)
                mainHandler.post { callback(Result.failure(e)) }
            }
        }
    }

    /**
     * 방송 제목 및 태그 실시간 업데이트 (치지직 스튜디오 PUT API)
     */
    fun updateLiveTitleAndTags(
        channelId: String,
        newTitle: String,
        newTags: List<String>,
        callback: (Result<Boolean>) -> Unit
    ) {
        val cookie = SecurityManager.getNaverCookie()
        if (cookie.isEmpty()) {
            callback(Result.failure(Exception("네이버 로그인이 필요합니다.")))
            return
        }

        if (newTitle.trim().isEmpty()) {
            callback(Result.failure(Exception("방송 제목을 입력하세요.")))
            return
        }

        thread {
            try {
                // 기존 조회된 raw content가 있으면 복제 후 수정, 없으면 기본 구조 생성
                val bodyJson = if (lastRawContent != null) {
                    JSONObject(lastRawContent.toString()).apply {
                        put("channelId", channelId)
                        put("defaultLiveTitle", newTitle.trim())
                        put("tags", JSONArray(newTags))
                    }
                } else {
                    JSONObject().apply {
                        put("channelId", channelId)
                        put("defaultLiveTitle", newTitle.trim())
                        put("tags", JSONArray(newTags))
                        put("paidPromotion", false)
                        put("clipActive", true)
                        put("adult", false)
                    }
                }

                val url = URL("https://api.chzzk.naver.com/manage/v1/channels/$channelId/live-setting/normal")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "PUT"
                    doOutput = true
                    setRequestProperty("Content-Type", "application/json; charset=UTF-8")
                    setRequestProperty("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")
                    setRequestProperty("Cookie", cookie)
                    setRequestProperty("Referer", "https://studio.chzzk.naver.com/")
                    connectTimeout = 7000
                    readTimeout = 7000
                }

                OutputStreamWriter(conn.outputStream, "UTF-8").use {
                    it.write(bodyJson.toString())
                    it.flush()
                }

                val responseCode = conn.responseCode
                if (responseCode == 200) {
                    val resp = BufferedReader(InputStreamReader(conn.inputStream)).use { it.readText() }
                    val json = JSONObject(resp)
                    val code = json.optInt("code", 0)
                    if (code == 200) {
                        Log.d("ChzzkStudio", "치지직 방송 제목/태그 변경 완료: $newTitle")
                        mainHandler.post { callback(Result.success(true)) }
                    } else {
                        val msg = json.optString("message", "오류코드 $code")
                        mainHandler.post { callback(Result.failure(Exception("변경 실패: $msg"))) }
                    }
                } else if (responseCode == 401 || responseCode == 403) {
                    mainHandler.post { callback(Result.failure(Exception("네이버 로그인 권한이 없습니다. 다시 로그인해주세요."))) }
                } else {
                    mainHandler.post { callback(Result.failure(Exception("방송 설정 변경 실패 (HTTP $responseCode)"))) }
                }
            } catch (e: Exception) {
                Log.e("ChzzkStudio", "방송 설정 변경 오류", e)
                mainHandler.post { callback(Result.failure(e)) }
            }
        }
    }

    /**
     * 로그인한 네이버 계정의 닉네임 조회
     */
    fun fetchUserNickname(callback: (String) -> Unit) {
        val cookie = SecurityManager.getNaverCookie()
        if (cookie.isEmpty()) {
            callback("")
            return
        }

        thread {
            try {
                val url = URL("https://comm-api.game.naver.com/nng_main/v1/user/getUserStatus")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    setRequestProperty("User-Agent", "Mozilla/5.0")
                    setRequestProperty("Cookie", cookie)
                    connectTimeout = 4000
                    readTimeout = 4000
                }
                if (conn.responseCode == 200) {
                    val resp = BufferedReader(InputStreamReader(conn.inputStream)).use { it.readText() }
                    val json = JSONObject(resp)
                    val content = json.optJSONObject("content")
                    val nickname = content?.optString("nickname", "") ?: ""
                    mainHandler.post { callback(nickname) }
                } else {
                    mainHandler.post { callback("") }
                }
            } catch (e: Exception) {
                mainHandler.post { callback("") }
            }
        }
    }
}
