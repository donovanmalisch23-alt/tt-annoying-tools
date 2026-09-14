package com.teamtalk.annoying.ui.screens

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.teamtalk.annoying.BuildConfig
import com.teamtalk.annoying.core.Sdk
import com.teamtalk.annoying.run.ToolRunManager
import com.teamtalk.annoying.tools.ToolRegistry
import com.teamtalk.annoying.tools.ToolSpec
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.ROUTE_TOOL_PREFIX
import com.teamtalk.annoying.ui.components.Badge
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.SectionTitle

@Composable
fun HomeScreen(viewModel: AppViewModel, navController: NavController) {
    val runState by ToolRunManager.state.collectAsState()
    val sdkVersion = remember { runCatching { Sdk.version() }.getOrDefault("not loaded") }
    val config = viewModel.config

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text("TT Annoying Tools", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Badge(BuildConfig.RELEASE_CHANNEL)
            Badge("experimental", MaterialTheme.colorScheme.secondary)
        }
        Spacer(Modifier.height(10.dp))
        Text(
            "TeamTalk 5 load and behaviour tests for a small tester group. " +
                "Run these only against servers you administer, or where the participants agreed.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        if (runState.status == ToolRunManager.Status.RUNNING) {
            SectionTitle("Running")
            Card(
                colors = CardDefaults.cardColors(
                    containerColor = MaterialTheme.colorScheme.primaryContainer,
                ),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Column(Modifier.padding(14.dp)) {
                    Text(
                        runState.toolTitle,
                        style = MaterialTheme.typography.titleMedium,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                    )
                    Spacer(Modifier.height(8.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = { navController.navigate("log") }) { Text("Open log") }
                        OutlinedButton(onClick = { ToolRunManager.requestStop() }) { Text("Stop") }
                    }
                }
            }
        }

        if (!viewModel.licenseAccepted) {
            InfoCard(
                title = "SDK license not accepted",
                body = "Accept the TeamTalk 5 SDK license before starting a run. " +
                    "Without it the native client cannot be used.",
                accent = MaterialTheme.colorScheme.secondary,
            )
        }

        SectionTitle("Target")
        Card(
            colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(Modifier.padding(14.dp)) {
                Text(
                    if (config.host.isBlank()) "No server configured" else config.host,
                    style = MaterialTheme.typography.titleMedium,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "TCP ${config.tcpPort} · UDP ${config.udpPort} · " +
                        (if (config.username.isBlank()) "anonymous" else config.username) +
                        " · ${if (config.encrypted) "encrypted" else "plain"}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(4.dp))
                Text(
                    "Allowlisted hosts: ${viewModel.whitelistEntries.size} · SDK $sdkVersion",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    OutlinedButton(onClick = { navController.navigate("connection") }) { Text("Edit server") }
                    OutlinedButton(onClick = { navController.navigate("allowlist") }) { Text("Allowlist") }
                }
            }
        }

        SectionTitle("Gentle tests")
        ToolRegistry.specs.filter { it.soft }.forEach { spec ->
            ToolCard(spec) { navController.navigate("$ROUTE_TOOL_PREFIX${spec.id}") }
        }

        SectionTitle("Heavy load tests")
        InfoCard(
            title = "These stress the server",
            body = "Idle bots, the combined suite, the local flood and the ramp test put real " +
                "load on a server. Use them only on your own infrastructure.",
            accent = MaterialTheme.colorScheme.secondary,
        )
        ToolRegistry.specs.filter { !it.soft }.forEach { spec ->
            ToolCard(spec) { navController.navigate("$ROUTE_TOOL_PREFIX${spec.id}") }
        }

        Spacer(Modifier.height(24.dp))
    }
}

@Composable
private fun ToolCard(spec: ToolSpec, onClick: () -> Unit) {
    Card(
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface),
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 5.dp)
            .clickable(onClick = onClick),
    ) {
        Row(
            modifier = Modifier.padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(
                    spec.title,
                    style = MaterialTheme.typography.titleMedium,
                    fontWeight = FontWeight.SemiBold,
                )
                Text(
                    spec.tagline,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Spacer(Modifier.width(8.dp))
            if (spec.requiresWhitelist) {
                Badge("whitelist", MaterialTheme.colorScheme.secondary)
            }
        }
    }
}
