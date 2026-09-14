package com.teamtalk.annoying.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.teamtalk.annoying.BuildConfig
import com.teamtalk.annoying.core.Sdk
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.SectionTitle

@Composable
fun AboutScreen(viewModel: AppViewModel) {
    val sdkVersion = remember { runCatching { Sdk.version() }.getOrDefault("not loaded") }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text("About", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(6.dp))
        Text(
            "TeamTalk Annoying Tools — Android alpha-soft",
            style = MaterialTheme.typography.titleSmall,
            color = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(10.dp))
        Text(
            "Version ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE}) · channel " +
                "${BuildConfig.RELEASE_CHANNEL}\nTeamTalk SDK: $sdkVersion",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        SectionTitle("What this is")
        InfoCard(
            title = "Port of the Linux SDK suite",
            body = "The desktop tools drive the TeamTalk 5 Python SDK. This app is the same set " +
                "of tests written against BearWare's TeamTalk Java SDK for Android: message and " +
                "login/leave-join tests, idle bots, a trigger-based response bot, the combined " +
                "suite, and the local flood / ramp capacity tests.",
        )

        SectionTitle("Before you start")
        InfoCard(
            title = "Consent and ownership",
            body = "Use these tools only on a TeamTalk server you own or administer, and only " +
                "where the participants have agreed to the test. Bulk actions and the heavy " +
                "tests require an explicit allowlist entry and a confirmation.",
            accent = MaterialTheme.colorScheme.secondary,
        )
        InfoCard(
            title = "No native redistribution",
            body = "The TeamTalk SDK is not bundled. Builds of this app link against the SDK that " +
                "the builder downloaded from BearWare.dk, and the SDK license must be accepted " +
                "on first run.",
        )
        InfoCard(
            title = "Android limits",
            body = "A desktop build can host thousands of idle bots across worker processes. " +
                "Android runs bots as threads in one process, so the count is capped and the " +
                "app says so instead of failing silently.",
        )

        SectionTitle("Credits")
        InfoCard(
            title = "Based on work by others",
            body = "The original project credited blindelectron, RD-Productions, Simpter and " +
                "Patrick Wilson. The TeamTalk SDK is by BearWare.dk.",
        )
        Spacer(Modifier.height(24.dp))
    }
}
