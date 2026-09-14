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
import com.teamtalk.annoying.tools.ToolRegistry
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.components.HeroHeader
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.SectionTitle

@Composable
fun AboutScreen(viewModel: AppViewModel) {
    val sdkStatus = remember { Sdk.statusLine() }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {
        HeroHeader(
            title = "About",
            subtitle = "TeamTalk Annoying Tools — Android alpha-soft",
            chips = listOf(BuildConfig.RELEASE_CHANNEL, "version ${BuildConfig.VERSION_NAME}"),
        )

        Column(Modifier.padding(16.dp)) {
            Text(
                text = sdkStatus,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            SectionTitle("What this is")
            InfoCard(
                title = "Port of the desktop suite",
                body = "The desktop tools drive the TeamTalk 5 Python SDK. This app is the " +
                    "same set of ${ToolRegistry.specs.size} tests written against BearWare's " +
                    "TeamTalk Java SDK for Android: message and login/leave-join tests, idle " +
                    "bots, a trigger-based response bot, the combined suite, and the local " +
                    "flood / ramp capacity tests.",
            )

            SectionTitle("Everything runs on the device")
            InfoCard(
                title = "No helper process",
                body = "The TeamTalk 5 SDK is linked into this app and every connection is " +
                    "opened from inside it. There is no desktop bridge, no local server and " +
                    "no second process to keep alive: close the app and nothing is left " +
                    "running.",
            )
            InfoCard(
                title = "Where the modes differ",
                body = "The desktop suite forks worker processes to stay under the native " +
                    "select() file-descriptor ceiling. A phone runs bots as threads in one " +
                    "process, so idle bots cap at 128 and the concurrent suite at 64; " +
                    "exceeding that is refused with a message instead of crashing.",
            )

            SectionTitle("Admin panel")
            InfoCard(
                title = "Allowlist, target and license",
                body = "The bottom of the Tools page opens the admin panel. It holds the " +
                    "exact-host allowlist, the target server, the SDK license decision and " +
                    "the reset actions, and it asks for the administrator credential set on " +
                    "first run. A restart locks it again.",
            )
            if (!viewModel.adminConfigured) {
                InfoCard(
                    title = "No administrator yet",
                    body = "Open the admin panel and set the administrator name and password " +
                        "before configuring a target.",
                    accent = MaterialTheme.colorScheme.secondary,
                )
            }

            SectionTitle("Before you start")
            InfoCard(
                title = "Consent and ownership",
                body = "Use these tools only on a TeamTalk server you own or administer, and " +
                    "only where the participants have agreed to the test. Bulk actions and " +
                    "the heavy tests require an allowlist entry and a confirmation.",
                accent = MaterialTheme.colorScheme.secondary,
            )
            InfoCard(
                title = "SDK license",
                body = "The SDK's own license agreement must be accepted on first run. " +
                    "Builds without a purchased registration key run in the SDK's trial " +
                    "mode. The SDK binaries are not committed to the repository; the tester " +
                    "APK links the SDK the builder downloaded from BearWare.dk.",
            )

            SectionTitle("Credits")
            InfoCard(
                title = "Based on work by others",
                body = "The original project credited blindelectron, RD-Productions, Simpter " +
                    "and Patrick Wilson. The TeamTalk SDK is by BearWare.dk.",
            )
            Spacer(Modifier.height(24.dp))
        }
    }
}
