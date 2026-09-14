import java.util.Properties

plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

// Optional tester signing. Values come from local.properties (never committed)
// or the environment, so no key material lives in the repository.
val localProps = Properties().apply {
    val file = rootProject.file("local.properties")
    if (file.exists()) file.inputStream().use { load(it) }
}

fun signingValue(property: String, env: String): String? =
    (localProps.getProperty(property) ?: System.getenv(env))?.takeIf { it.isNotBlank() }

android {
    namespace = "com.teamtalk.annoying"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.teamtalk.annoying"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "0.1.0-alpha-soft"

        // The release channel is surfaced in the UI and run logs so testers
        // always know which build they are running.
        buildConfigField("String", "RELEASE_CHANNEL", "\"alpha-soft\"")
        buildConfigField("String", "SDK_VERSION_URL", "\"https://bearware.dk/?page_id=419\"")
    }

    signingConfigs {
        create("tester") {
            val storePath = signingValue("tt.keystore", "TT_KEYSTORE")
            if (storePath != null) {
                storeFile = rootProject.file(storePath)
                storePassword = signingValue("tt.keystore.password", "TT_KEYSTORE_PASSWORD")
                keyAlias = signingValue("tt.key.alias", "TT_KEY_ALIAS")
                keyPassword = signingValue("tt.key.password", "TT_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        debug {
            applicationIdSuffix = ".debug"
            versionNameSuffix = "-debug"
            isMinifyEnabled = false
        }
        release {
            // Alpha build for a small tester group: keep symbols readable so
            // crash reports from testers stay usable.
            isMinifyEnabled = false
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
            val tester = signingConfigs.getByName("tester")
            if (tester.storeFile != null) signingConfig = tester
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes += setOf(
            "/META-INF/{AL2.0,LGPL2.1}",
            "/META-INF/DEPENDENCIES",
            "/META-INF/LICENSE*",
        )
    }

    // Native TeamTalk SDK libraries live here, one folder per ABI,
    // e.g. src/main/jniLibs/arm64-v8a/libTeamTalk5-jni.so
    sourceSets["main"].jniLibs.srcDirs("src/main/jniLibs")
}

dependencies {
    // Java TeamTalk bindings bundled by the TeamTalk 5 Android SDK.
    // Drop TeamTalk5.jar into android/app/libs/ (see android/README.md).
    implementation(fileTree(mapOf("dir" to "libs", "include" to listOf("*.jar"))))

    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.navigation.compose)
    implementation(libs.kotlinx.coroutines.android)

    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.graphics)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    implementation(libs.androidx.material.icons.extended)

    debugImplementation(libs.androidx.ui.tooling)

    testImplementation(libs.junit)
    androidTestImplementation(libs.androidx.junit)
    androidTestImplementation(libs.androidx.espresso.core)
    androidTestImplementation(platform(libs.androidx.compose.bom))
}

// The SDK license text is the one already vendored for the Linux tools; copy
// it into assets at build time instead of keeping a second copy in git.
val copySdkLicense by tasks.registering(Copy::class) {
    val source = rootProject.file("../sdk/License.txt")
    onlyIf { source.exists() }
    from(source)
    into(layout.projectDirectory.dir("src/main/assets"))
    rename { "teamtalk-sdk-license.txt" }
}

tasks.matching { it.name == "preBuild" }.configureEach { dependsOn(copySdkLicense) }
