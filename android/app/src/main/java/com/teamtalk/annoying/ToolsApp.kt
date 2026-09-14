package com.teamtalk.annoying

import android.app.Application
import com.teamtalk.annoying.core.AdminAuth
import com.teamtalk.annoying.core.ConfigStore

class ToolsApp : Application() {

    val store: ConfigStore by lazy { ConfigStore(this) }

    /** Device-local administrator credential for the admin panel. */
    val admin: AdminAuth by lazy { AdminAuth(store) }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    companion object {
        lateinit var instance: ToolsApp
            private set
    }
}
