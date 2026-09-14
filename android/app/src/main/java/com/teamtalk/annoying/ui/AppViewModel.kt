package com.teamtalk.annoying.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import com.teamtalk.annoying.ToolsApp
import com.teamtalk.annoying.core.ConfigStore
import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.Whitelist

/** Single source of truth for the settings screens, backed by [ConfigStore]. */
class AppViewModel(application: Application) : AndroidViewModel(application) {

    private val store: ConfigStore = (application as ToolsApp).store

    var config by mutableStateOf(store.loadConfig())
        private set

    var whitelistText by mutableStateOf(store.loadWhitelistText())
        private set

    var licenseAccepted by mutableStateOf(store.isSdkLicenseAccepted())
        private set

    val whitelistEntries: List<String>
        get() = Whitelist.parse(whitelistText)

    fun updateConfig(updated: ConnectionConfig) {
        config = updated
        store.saveConfig(updated)
    }

    fun updateWhitelist(text: String) {
        whitelistText = text
        store.saveWhitelistText(text)
    }

    fun acceptLicense() {
        store.setSdkLicenseAccepted(true)
        licenseAccepted = true
    }

    fun declineLicense() {
        store.setSdkLicenseAccepted(false)
        licenseAccepted = false
    }
}
