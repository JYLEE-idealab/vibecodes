plugins {
    id("com.android.application")
}

android {
    namespace = "com.example.securelivestreamingapp"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.example.securelivestreamingapp"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        ndk {
            abiFilters += listOf("arm64-v8a", "armeabi-v7a")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
            signingConfig = signingConfigs.getByName("debug")
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("com.google.android.material:material:1.11.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")

    // RootEncoder: SRT 스트리밍
    implementation("com.github.pedroSG94.RootEncoder:library:2.4.3")
    implementation("com.github.pedroSG94.RootEncoder:srt:2.4.3")

    // UVCAndroid: 유저스페이스 USB UVC 카메라 엔진 (삼성 갤럭시 및 모든 안드로이드 지원)
    implementation("com.herohan:UVCAndroid:1.0.13")

    // Security Crypto: AES 암호화 로컬 저장소
    implementation("androidx.security:security-crypto:1.1.0-alpha06")

    // WebSocket (OBS WebSocket v5 + 치지직 채팅)
    implementation("org.java-websocket:Java-WebSocket:1.5.4")
}

java {
    toolchain {
        languageVersion.set(JavaLanguageVersion.of(17))
    }
}
