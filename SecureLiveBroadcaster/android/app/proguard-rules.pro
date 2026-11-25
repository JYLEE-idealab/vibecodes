# Proguard rules for SecureLiveStreamingApp

# RootEncoder
-keep class com.pedro.** { *; }
-dontwarn com.pedro.**

# Java-WebSocket
-keep class org.java_websocket.** { *; }
-dontwarn org.java_websocket.**

# AndroidX Security Crypto
-keep class androidx.security.crypto.** { *; }
-dontwarn androidx.security.crypto.**

# UVCAndroid
-keep class com.herohan.uvcapp.** { *; }
-dontwarn com.herohan.uvcapp.**
-keep class com.serenegiant.** { *; }
-dontwarn com.serenegiant.**
