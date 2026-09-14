package com.teamtalk.annoying.ui

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import com.teamtalk.annoying.ToolsApp
import com.teamtalk.annoying.core.AdminAuth
import com.teamtalk.annoying.core.ConfigStore
import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.Whitelist

/** Single source of truth for the settings screens, backed by [ConfigStore]. */
class AppViewModel(application: Application) : AndroidViewModel(application) {

    private val store: ConfigStore = (application as ToolsApp).store
    private val adminAuth: AdminAuth = (application as ToolsApp).admin

    var config by mutableStateOf(store.loadConfig())
        private set

    var whitelistText by mutableStateOf(store.loadWhitelistText())
        private set

    var licenseAccepted by mutableStateOf(store.isSdkLicenseAccepted())
        private set

    /**
     * The admin panel stays unlocked for this process only, so leaving the app
     * (or a restart) locks it again.
     */
    var adminUnlocked by mutableStateOf(false)
        private set

    var adminUser by mutableStateOf<String?>(null)
        private set

    val adminConfigured: Boolean get() = adminAuth.configured

    val storedAdminName: String? get() = adminAuth.username

    val whitelistEntries: List<String>
        get() = Whitelist.parse(whitelistText)

    val hostAllowlisted: Boolean
        get() = config.host.isNotBlank() && Whitelist.isAllowed(config.host, whitelistEntries)

    fun updateConfig(updated: ConnectionConfig) {
        config = updated
        store.saveConfig(updated)
    }

    fun updateWhitelist(text: String) {
        whitelistText = text
        store.saveWhitelistText(text)
    }

    /** Adds one host, keeping the comments already in the file. Returns an error, or null. */
    fun addHost(rawHost: String): String? {
        val host = Whitelist.normalize(rawHost)
        if (host.isEmpty()) return "Enter a hostname or IP address."
        if (!HOST_PATTERN.matches(host)) {
            return "'$rawHost' does not look like a hostname or an IP address."
        }
        if (host in whitelistEntries) return "'$host' is already on the allowlist."
        val separator = if (whitelistText.isEmpty() || whitelistText.endsWith("\n")) "" else "\n"
        updateWhitelist(whitelistText + separator + host + "\n")
        return null
    }

    fun removeHost(host: String) {
        val target = Whitelist.normalize(host)
        val kept = whitelistText.lineSequence().filterNot { line ->
            val body = line.substringBefore('#').trim()
            body.isNotEmpty() && Whitelist.normalize(body) == target
        }
        updateWhitelist(kept.joinToString("\n"))
    }

    fun resetWhitelist() {
        store.resetWhitelist()
        whitelistText = store.loadWhitelistText()
    }

    fun acceptLicense() {
        store.setSdkLicenseAccepted(true)
        licenseAccepted = true
    }

    fun declineLicense() {
        store.setSdkLicenseAccepted(false)
        licenseAccepted = false
    }

    // ----- admin panel ------------------------------------------------------ //

    /** Creates the admin credential and unlocks the panel. Returns an error, or null. */
    fun adminProvision(username: String, password: String, confirm: String): String? {
        val error = adminAuth.create(username, password, confirm)
        if (error != null) return error
        adminUser = adminAuth.username
        adminUnlocked = true
        return null
    }

    /** Returns an error message, or null when the panel is now unlocked. */
    fun adminSignIn(username: String, password: String): String? = when (adminAuth.signIn(username, password)) {
        AdminAuth.SignIn.OK -> {
            adminUser = adminAuth.username
            adminUnlocked = true
            null
        }
        AdminAuth.SignIn.BAD_CREDENTIALS -> "Wrong administrator name or password."
        AdminAuth.SignIn.NOT_CONFIGURED -> "No administrator has been set up yet."
        AdminAuth.SignIn.BAD_INPUT -> "Enter the administrator name and password."
        AdminAuth.SignIn.LOCKED ->
            "Too many failed attempts. Try again in ${adminAuth.lockRemainingSeconds()} s."
    }

    /** Seconds left on the sign-in lockout (0 when sign-in is allowed). */
    fun adminLockRemainingSeconds(): Long = adminAuth.lockRemainingSeconds()

    fun adminLock() {
        adminUnlocked = false
        adminUser = null
    }

    /** Forgets the credential entirely; the next visit asks for a new one. */
    fun adminForgetCredential() {
        adminAuth.reset()
        adminLock()
    }

    private companion object {
        val HOST_PATTERN = Regex("^[a-z0-9._:\\-]{1,253}$")
    }
}
