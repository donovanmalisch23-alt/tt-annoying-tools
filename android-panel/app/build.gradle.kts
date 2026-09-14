import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

// Optional release signing. Values come from local.properties (never committed)
// or the environment, so no key material lives in the repository — same scheme
// as the sibling `android/` module.
val localProps = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

fun signingValue(property: String, env: String): String? =
    (localProps.getProperty(property) ?: System.getenv(env))?.takeIf { it.isNotBlank() }

android {
    namespace = "com.teamtalk.webbypanel"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.teamtalk.webbypanel"
        // 26 is the floor for two reasons: the wrapper ships its launcher icon as
        // an adaptive icon only, and older WebViews cannot run the panel's
        // ES2020 bundle.
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-alpha-soft"

        // Shown in the wrapper's header so a test build is never mistaken for a
        // finished one.
        buildConfigField("String", "RELEASE_CHANNEL", "\"alpha-soft\"")
    }

    signingConfigs {
        create("tester") {
            val storePath = signingValue("panel.keystore", "WEBBY_KEYSTORE")
            if (storePath != null) {
                storeFile = rootProject.file(storePath)
                storePassword = signingValue("panel.keystore.password", "WEBBY_KEYSTORE_PASSWORD")
                keyAlias = signingValue("panel.key.alias", "WEBBY_KEY_ALIAS")
                keyPassword = signingValue("panel.key.password", "WEBBY_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            // No applicationIdSuffix: the debug APK is the alpha artifact we
            // hand out, so its identity must match a future signed release and
            // testers must not lose the panel's stored settings on upgrade.
            isMinifyEnabled = false
        }
        release {
            isMinifyEnabled = false
            val tester = signingConfigs.getByName("tester")
            if (tester.storeFile != null) signingConfig = tester
        }
    }

    buildFeatures {
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    packaging {
        resources.excludes += setOf("/META-INF/{AL2.0,LGPL2.1}")
    }
}

// No `dependencies` block on purpose: the wrapper uses framework classes only,
// which keeps the APK small and the build free of version conflicts.
