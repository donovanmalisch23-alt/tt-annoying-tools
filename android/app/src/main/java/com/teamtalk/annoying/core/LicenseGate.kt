package com.teamtalk.annoying.core

import android.content.Context

/**
 * Enforces first-run acceptance of the bundled TeamTalk 5 SDK license, the
 * Android equivalent of `ensure_sdk_license_accepted()` in tt_teamtalk.py.
 *
 * The UI shows [licenseText] and records the decision through
 * [ConfigStore.setSdkLicenseAccepted]; every tool session calls
 * [requireAccepted] before touching the SDK.
 */
object LicenseGate {

    private const val ASSET_NAME = "teamtalk-sdk-license.txt"

    private const val FALLBACK_TEXT =
        "Use of the TeamTalk 5 SDK is not permitted until you have read and agreed to the " +
            "TeamTalk 5 SDK License Agreement from BearWare.dk. See https://bearware.dk/?page_id=419 " +
            "for the full terms."

    /** The bundled license text, or a short pointer if the asset is missing. */
    fun licenseText(context: Context): String = try {
        context.assets.open(ASSET_NAME).bufferedReader().use { it.readText() }
    } catch (t: Throwable) {
        FALLBACK_TEXT
    }

    /**
     * Blocks SDK use until the license is accepted. Throws
     * [SdkLicenseRequiredException] so the caller can prompt the user.
     */
    fun requireAccepted(store: ConfigStore) {
        if (!store.isSdkLicenseAccepted()) throw SdkLicenseRequiredException()
    }
}
