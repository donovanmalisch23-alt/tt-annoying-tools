package com.teamtalk.annoying.core

import dk.bearware.TeamTalk5
import dk.bearware.TeamTalkBase

/**
 * Thin wrapper over the TeamTalk Java SDK loader.
 *
 * `TeamTalk5.loadLibrary()` triggers the class initializer that runs
 * `System.loadLibrary("TeamTalk5-jni")`. The static helpers on TeamTalkBase
 * do *not* load it, so every entry point funnels through [ensureLoaded] first.
 */
object Sdk {

    @Volatile
    private var loaded = false

    @Volatile
    private var loadFailure: String? = null

    @Synchronized
    fun ensureLoaded() {
        if (loaded) return
        loadFailure?.let { throw TeamTalkSdkException(it) }
        try {
            TeamTalk5.loadLibrary()
            loaded = true
        } catch (t: Throwable) {
            val message =
                "The TeamTalk 5 Android SDK was not found in this build. Copy the SDK's " +
                    "TeamTalk5.jar into android/app/libs/ and its libTeamTalk5-jni.so into " +
                    "android/app/src/main/jniLibs/<abi>/ (see android/README.md). " +
                    "Underlying error: ${t.message ?: t::class.java.simpleName}"
            loadFailure = message
            throw TeamTalkSdkException(message, t)
        }
    }

    fun version(): String {
        ensureLoaded()
        return try {
            TeamTalkBase.getVersion() ?: "unknown"
        } catch (t: Throwable) {
            "unavailable"
        }
    }

    /**
     * Activates a purchased SDK license key. No-op when no name is configured,
     * so trial builds behave exactly as before.
     */
    fun applyLicense(name: String?, key: String): Boolean {
        val licenseName = name?.takeIf { it.isNotBlank() } ?: return false
        ensureLoaded()
        return try {
            val ok = TeamTalkBase.setLicenseInformation(licenseName, key)
            if (ok) LogBus.log("[license] SDK license accepted; trial mode disabled.")
            else LogBus.log("[license] SDK rejected the license key; running in TRIAL MODE.")
            ok
        } catch (t: Throwable) {
            LogBus.log("[license] setLicenseInformation failed: ${t.message}")
            false
        }
    }

    /** Human-readable description for a ClientError code. */
    fun errorText(code: Int): String {
        if (code < 0) return "unknown error"
        return try {
            TeamTalkBase.getErrorMessage(code)?.takeIf { it.isNotBlank() } ?: "SDK error $code"
        } catch (t: Throwable) {
            "SDK error $code"
        }
    }
}
