package com.teamtalk.annoying

import android.app.Application
import com.teamtalk.annoying.core.ConfigStore

class ToolsApp : Application() {

    val store: ConfigStore by lazy { ConfigStore(this) }

    override fun onCreate() {
        super.onCreate()
        instance = this
    }

    companion object {
        lateinit var instance: ToolsApp
            private set
    }
}
