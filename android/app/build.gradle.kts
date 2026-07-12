plugins {
    id("com.android.application")
}

android {
    namespace = "com.aikid.terminal"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.aikid.terminal"
        minSdk = 26
        targetSdk = 36
        versionCode = providers.gradleProperty("APP_VERSION_CODE").orElse("1").get().toInt()
        versionName = providers.gradleProperty("APP_VERSION_NAME").orElse("0.1.0").get()
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"

        val apiBaseUrl = providers.gradleProperty("API_BASE_URL")
            .orElse("https://api.invalid")
            .get()
        val expectedSigner = providers.gradleProperty("EXPECTED_SIGNER_SHA256")
            .orElse("")
            .get()
        val bootstrapEnrollmentToken = providers.gradleProperty("BOOTSTRAP_ENROLLMENT_TOKEN")
            .orElse("")
            .get()
        val legacyApiBaseUrl = providers.gradleProperty("LEGACY_API_BASE_URL")
            .orElse("")
            .get()
        buildConfigField("String", "API_BASE_URL", "\"$apiBaseUrl\"")
        buildConfigField("String", "EXPECTED_SIGNER_SHA256", "\"$expectedSigner\"")
        buildConfigField("String", "LEGACY_API_BASE_URL", "\"$legacyApiBaseUrl\"")
        buildConfigField(
            "String",
            "BOOTSTRAP_ENROLLMENT_TOKEN",
            "\"$bootstrapEnrollmentToken\"",
        )
    }

    buildFeatures {
        buildConfig = true
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            val storePath = providers.environmentVariable("ANDROID_SIGNING_STORE_FILE").orNull
            if (storePath != null) {
                signingConfig = signingConfigs.create("production") {
                    storeFile = file(storePath)
                    storePassword = providers.environmentVariable("ANDROID_SIGNING_STORE_PASSWORD").get()
                    keyAlias = providers.environmentVariable("ANDROID_SIGNING_KEY_ALIAS").get()
                    keyPassword = providers.environmentVariable("ANDROID_SIGNING_KEY_PASSWORD").get()
                }
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation("androidx.activity:activity-ktx:1.13.0")
    implementation("androidx.core:core-ktx:1.19.0")
    implementation("androidx.work:work-runtime:2.11.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.11.0")
    implementation(platform("com.squareup.okhttp3:okhttp-bom:5.4.0"))
    implementation("com.squareup.okhttp3:okhttp")

    testImplementation("junit:junit:4.13.2")
}
