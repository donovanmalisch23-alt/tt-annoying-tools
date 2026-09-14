package com.teamtalk.webbypanel

import android.content.Context

/**
 * The wrapper's own settings — one value, the bridge address.
 *
 * Everything the panel itself configures (connection details, the allowlist,
 * the run log) lives in the panel, in the WebView's `localStorage`, which is why
 * this is so small. The address is kept here rather than only in the panel
 * because the operator sets it once, from the app, and expects it to survive a
 * "clear panel data".
 */
class PanelPrefs(context: Context) {
    private val prefs = context.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    /** `""` means "leave the panel on its in-app simulator". */
    var bridgeUrl: String
        get() = prefs.getString(KEY_BRIDGE_URL, "")?.trim().orEmpty()
        set(value) {
            prefs.edit().putString(KEY_BRIDGE_URL, value.trim()).apply()
        }

    private companion object {
        const val NAME = "webby-panel"
        const val KEY_BRIDGE_URL = "bridge_url"
    }
}
