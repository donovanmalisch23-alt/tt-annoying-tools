package com.teamtalk.annoying.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.teamtalk.annoying.BuildConfig
import com.teamtalk.annoying.core.Sdk
import com.teamtalk.annoying.run.ToolRunManager
import com.teamtalk.annoying.tools.ToolRegistry
import com.teamtalk.annoying.tools.ToolSpec
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.ROUTE_ADMIN
import com.teamtalk.annoying.ui.ROUTE_TOOL_PREFIX
import com.teamtalk.annoying.ui.components.HeroHeader
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.PanelButton
import com.teamtalk.annoying.ui.components.SectionTitle
import com.teamtalk.annoying.ui.components.StatTile
import com.teamtalk.annoying.ui.components.ToolRow
import com.teamtalk.annoying.ui.components.adminPanelIcon
import com.teamtalk.annoying.ui.components.toolIcon

@Composable
fun HomeScreen(viewModel: AppViewModel, navController: NavController) {
    val runState by ToolRunManager.state.collectAsState()
    val sdkStatus = remember { Sdk.statusLine() }
    val sdkAvailable = remember { Sdk.isAvailable() }
    val config = viewModel.config
    val gentle = remember { ToolRegistry.specs.filter { it.soft } }
    val heavy = remember { ToolRegistry.specs.filter { !it.soft } }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {
        HeroHeader(
            title = "TT Annoying Tools",
            subtitle = "TeamTalk 5 load and behaviour tests that run entirely on this " +
                "device — the SDK is linked straight into the app, so nothing is driven " +
                "by a desktop helper process.",
            chips = listOf(
                BuildConfig.RELEASE_CHANNEL,
                if (sdkAvailable) "SDK on device" else "SDK not loaded",
            ),
        )

        Column(Modifier.padding(horizontal = 16.dp)) {
            Spacer(Modifier.height(14.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                StatTile(
                    label = "Target",
                    value = config.host.ifBlank { "not set" },
                    modifier = Modifier.weight(1f),
                )
                StatTile(
                    label = "Allowlist",
                    value = "${viewModel.whitelistEntries.size} host(s)",
                    modifier = Modifier.weight(1f),
                )
            }
            Spacer(Modifier.height(10.dp))
            Text(
                text = sdkStatus,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            if (runState.status == ToolRunManager.Status.RUNNING) {
                Spacer(Modifier.height(16.dp))
                Card(
                    colors = CardDefaults.cardColors(
                        containerColor = MaterialTheme.colorScheme.primaryContainer,
                    ),
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Column(Modifier.padding(14.dp)) {
                        Text(
                            text = "Running: ${runState.toolTitle}",
                            style = MaterialTheme.typography.titleMedium,
                            color = MaterialTheme.colorScheme.onPrimaryContainer,
                            fontWeight = FontWeight.SemiBold,
                        )
                        Spacer(Modifier.height(10.dp))
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            Button(onClick = { navController.navigate("log") }) { Text("Open log") }
                            OutlinedButton(onClick = { ToolRunManager.requestStop() }) { Text("Stop") }
                        }
                    }
                }
            }

            if (!viewModel.licenseAccepted) {
                Spacer(Modifier.height(10.dp))
                InfoCard(
                    title = "SDK license not accepted",
                    body = "Accept the TeamTalk 5 SDK license in the admin panel before " +
                        "starting a run. Without it the native client cannot be used.",
                    accent = MaterialTheme.colorScheme.secondary,
                )
            }

            Spacer(Modifier.height(24.dp))
            Text(
                text = "Select a tool below",
                style = MaterialTheme.typography.headlineSmall,
                fontWeight = FontWeight.Bold,
            )
            Spacer(Modifier.height(4.dp))
            Text(
                text = "${ToolRegistry.specs.size} tests · ${gentle.size} gentle · " +
                    "${heavy.size} heavy load",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            SectionTitle("Gentle tests")
            Text(
                text = "Single connections and short sequences. Safe against any server " +
                    "you are allowed to test.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            gentle.forEach { spec ->
                ToolRow(
                    icon = toolIcon(spec.id),
                    title = spec.title,
                    subtitle = spec.tagline,
                    badges = toolBadges(spec),
                    onClick = { navController.navigate("$ROUTE_TOOL_PREFIX${spec.id}") },
                )
            }

            SectionTitle("Heavy load tests")
            InfoCard(
                title = "These stress the server",
                body = "Idle bots, the combined suite, the local flood and the ramp test put " +
                    "real load on a server. They need an allowlisted host and an explicit " +
                    "confirmation, and they belong on your own infrastructure.",
                accent = MaterialTheme.colorScheme.secondary,
            )
            heavy.forEach { spec ->
                ToolRow(
                    icon = toolIcon(spec.id),
                    title = spec.title,
                    subtitle = spec.tagline,
                    badges = toolBadges(spec),
                    onClick = { navController.navigate("$ROUTE_TOOL_PREFIX${spec.id}") },
                )
            }

            Spacer(Modifier.height(26.dp))
            PanelButton(
                icon = adminPanelIcon,
                title = "Admin panel",
                subtitle = "Allowlist, server target and SDK — administrator sign-in required",
                onClick = { navController.navigate(ROUTE_ADMIN) },
            )
            Spacer(Modifier.height(30.dp))
        }
    }
}

/** At most two chips per row, so the list keeps its rhythm. */
@Composable
private fun toolBadges(spec: ToolSpec): List<Pair<String, Color>> = buildList {
    add((if (spec.soft) "gentle" else "heavy load") to MaterialTheme.colorScheme.secondary)
    if (spec.requiresWhitelist) add("allowlist" to MaterialTheme.colorScheme.primary)
}
