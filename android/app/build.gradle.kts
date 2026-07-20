plugins {
    id("com.android.application")
}

android {
    namespace = "org.akshrava.app"
    compileSdk = 36

    defaultConfig {
        applicationId = "org.akshrava.app"
        // Android 8 covers the intended 2018–2021 donated-phone cohort.
        minSdk = 26
        targetSdk = 36
        versionCode = 12
        versionName = "0.2.12"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }

    val releaseKeystorePath = providers.environmentVariable("ANDROID_KEYSTORE_PATH").orNull
    val releaseKeystorePassword = providers.environmentVariable("ANDROID_KEYSTORE_PASSWORD").orNull
    val releaseKeyAlias = providers.environmentVariable("ANDROID_KEY_ALIAS").orNull
    val releaseKeyPassword = providers.environmentVariable("ANDROID_KEY_PASSWORD").orNull
    val releaseSigningConfigured = listOf(
        releaseKeystorePath, releaseKeystorePassword, releaseKeyAlias, releaseKeyPassword
    ).all { !it.isNullOrBlank() }

    if (providers.environmentVariable("REQUIRE_RELEASE_SIGNING").orNull == "true" && !releaseSigningConfigured) {
        throw GradleException("Release signing is required but Android keystore environment variables are incomplete")
    }

    if (releaseSigningConfigured) {
        signingConfigs.create("release") {
            storeFile = file(releaseKeystorePath!!)
            storePassword = releaseKeystorePassword
            keyAlias = releaseKeyAlias
            keyPassword = releaseKeyPassword
        }
    }

    buildTypes {
        debug {
            // Supervised GCP pilot WSS — volunteer screen remains editable.
            buildConfigField(
                "String",
                "DEFAULT_WSS_ENDPOINT",
                "\"wss://akshrava-api-c7d3j4nzdq-uc.a.run.app/v1/session\""
            )
        }
        release {
            // Keep minify off until ProGuard/R8 keep rules are audited for CameraX, OkHttp, and TTS.
            // Enabling without that audit risks stripping reflection-heavy release paths.
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            if (releaseSigningConfigured) signingConfig = signingConfigs.getByName("release")
            // Release phones must have a real, secure endpoint by default. Provisioning may
            // still override this value, but example.invalid made every unconfigured release
            // build silently fail before it could reach the backend.
            buildConfigField(
                "String",
                "DEFAULT_WSS_ENDPOINT",
                "\"wss://akshrava-api-c7d3j4nzdq-uc.a.run.app/v1/session\""
            )
        }
    }

    // AGP 8+ no longer generates BuildConfig by default. MainActivity gates ws:// (cleartext,
    // emulator-only) behind BuildConfig.DEBUG, so this must be explicit or the module fails to
    // compile with "Unresolved reference: BuildConfig".
    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    testOptions {
        unitTests.isReturnDefaultValues = true
    }
}

dependencies {
    val cameraX = "1.6.1"
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.activity:activity-ktx:1.10.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.lifecycle:lifecycle-service:2.11.0")
    implementation("androidx.camera:camera-core:$cameraX")
    implementation("androidx.camera:camera-camera2:$cameraX")
    implementation("androidx.camera:camera-lifecycle:$cameraX")
    implementation("com.squareup.okhttp3:okhttp:5.4.0")
    implementation("androidx.media:media:1.7.0")
    testImplementation("junit:junit:4.13.2")
    testImplementation("org.mockito:mockito-core:5.11.0")
    testImplementation("org.json:json:20240303")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test:core-ktx:1.6.1")
}
