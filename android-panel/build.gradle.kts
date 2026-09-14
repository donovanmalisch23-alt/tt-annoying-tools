// The Android wrapper for the web panel (see ../README.md). It is deliberately
// its own Gradle build: the `android/` module links the native TeamTalk SDK and
// only builds when you drop the SDK in, while this one must always build — it
// has no dependencies beyond AGP and Kotlin.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
}
