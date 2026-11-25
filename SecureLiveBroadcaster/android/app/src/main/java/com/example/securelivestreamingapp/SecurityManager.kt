package com.example.securelivestreamingapp

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

object SecurityManager {
    private const val PREF_FILE_NAME = "secure_stream_prefs"
    private lateinit var encryptedPrefs: SharedPreferences

    fun init(context: Context) {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()

        encryptedPrefs = EncryptedSharedPreferences.create(
            context,
            PREF_FILE_NAME,
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
        )
    }

    fun saveConfig(
        url: String,
        streamId: String,
        passphrase: String,
        obsHost: String = "",
        obsPort: Int = 4455,
        obsPass: String = "",
        chzzkChannelId: String = ""
    ) {
        encryptedPrefs.edit().apply {
            putString("SRT_URL", url)
            putString("SRT_STREAM_ID", streamId)
            putString("SRT_PASSPHRASE", passphrase)
            putString("OBS_HOST", obsHost)
            putInt("OBS_PORT", obsPort)
            putString("OBS_PASSWORD", obsPass)
            putString("CHZZK_CHANNEL_ID", chzzkChannelId)
            apply()
        }
    }

    fun saveSrtConfig(url: String, streamId: String, passphrase: String) {
        encryptedPrefs.edit().apply {
            putString("SRT_URL", url)
            putString("SRT_STREAM_ID", streamId)
            putString("SRT_PASSPHRASE", passphrase)
            apply()
        }
    }

    fun saveObsConfig(host: String, port: Int, pass: String) {
        encryptedPrefs.edit().apply {
            putString("OBS_HOST", host)
            putInt("OBS_PORT", port)
            putString("OBS_PASSWORD", pass)
            apply()
        }
    }

    fun saveChzzkConfig(channelId: String) {
        encryptedPrefs.edit().apply {
            putString("CHZZK_CHANNEL_ID", channelId)
            apply()
        }
    }

    fun saveNaverAccount(cookie: String, nickname: String = "") {
        encryptedPrefs.edit().apply {
            putString("NAVER_COOKIE", cookie)
            putString("NAVER_NICKNAME", nickname)
            apply()
        }
    }

    fun clearNaverAccount() {
        encryptedPrefs.edit().apply {
            remove("NAVER_COOKIE")
            remove("NAVER_NICKNAME")
            apply()
        }
    }

    fun getNaverCookie(): String = encryptedPrefs.getString("NAVER_COOKIE", "") ?: ""
    fun getNaverNickname(): String = encryptedPrefs.getString("NAVER_NICKNAME", "") ?: ""
    fun isNaverLoggedIn(): Boolean = getNaverCookie().isNotEmpty()

    fun getSrtUrl(): String = encryptedPrefs.getString("SRT_URL", "") ?: ""
    fun getSrtStreamId(): String = encryptedPrefs.getString("SRT_STREAM_ID", "") ?: ""
    fun getSrtPassphrase(): String = encryptedPrefs.getString("SRT_PASSPHRASE", "") ?: ""

    fun getObsHost(): String = encryptedPrefs.getString("OBS_HOST", "") ?: ""
    fun getObsPort(): Int = encryptedPrefs.getInt("OBS_PORT", 4455)
    fun getObsPassword(): String = encryptedPrefs.getString("OBS_PASSWORD", "") ?: ""

    fun getChzzkChannelId(): String = encryptedPrefs.getString("CHZZK_CHANNEL_ID", "") ?: ""
}
