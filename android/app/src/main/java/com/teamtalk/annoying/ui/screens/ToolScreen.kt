package com.teamtalk.annoying.ui.screens

import android.content.Context
import android.content.Intent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.navigation.NavController
import com.teamtalk.annoying.run.ToolRunManager
import com.teamtalk.annoying.service.RunService
import com.teamtalk.annoying.tools.FieldKind
import com.teamtalk.annoying.tools.FieldSpec
import com.teamtalk.annoying.tools.ToolRegistry
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.components.Badge
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.LabeledTextField
import com.teamtalk.annoying.ui.components.SectionTitle
import com.teamtalk.annoying.ui.components.ToggleRow

@Composable
fun ToolScreen(viewModel: AppViewModel, navController: NavController, toolId: String) {
    val spec = remember(toolId) { runCatching { ToolRegistry.spec(toolId) }.getOrNull() }
    if (spec == null) {
        Column(Modifier.fillMaxSize().padding(16.dp)) {
            Text("Unknown tool: $toolId", color = MaterialTheme.colorScheme.error)
        }
        return
    }

    val context = LocalContext.current
    val runState by ToolRunManager.state.collectAsState()
    var values by remember(toolId) { mutableStateOf(ToolRegistry.defaultValues(toolId)) }
    var confirmed by remember(toolId) { mutableStateOf(!spec.requiresConfirm) }
    var error by remember(toolId) { mutableStateOf<String?>(null) }

    val anyRunning = runState.status == ToolRunManager.Status.RUNNING
    val thisRunning = anyRunning && runState.toolId == toolId
    val hostMissing = viewModel.config.host.isBlank()
    val canRun = viewModel.licenseAccepted && !anyRunning && confirmed && !hostMissing

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text(spec.title, style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(8.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Badge(if (spec.soft) "gentle" else "heavy load", MaterialTheme.colorScheme.secondary)
            if (spec.requiresWhitelist) Badge("allowlist", MaterialTheme.colorScheme.primary)
        }
        Spacer(Modifier.height(10.dp))
        Text(
            spec.description,
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        if (!viewModel.licenseAccepted) {
            InfoCard(
                title = "SDK license not accepted",
                body = "Accept the TeamTalk 5 SDK license on the Tools tab before running anything.",
                accent = MaterialTheme.colorScheme.secondary,
            )
        }
        if (hostMissing) {
            InfoCard(
                title = "No server configured",
                body = "Set the server host on the Server tab first.",
                accent = MaterialTheme.colorScheme.secondary,
            )
        }
        if (spec.requiresWhitelist) {
            InfoCard(
                title = "Allowlist required",
                body = "This test refuses any host that is not listed on the Allowlist tab. " +
                    "Currently ${viewModel.whitelistEntries.size} host(s) are allowed.",
            )
        }

        SectionTitle("Parameters")
        spec.fields.forEach { field ->
            FieldInput(
                field = field,
                value = values[field.key] ?: field.default,
                onChange = { values = values + (field.key to it) },
            )
        }

        if (spec.requiresConfirm) {
            SectionTitle("Confirmation")
            ToggleRow(
                label = "I confirm this test may run against this server",
                checked = confirmed,
                onCheckedChange = { confirmed = it },
                help = "Only run load tests on servers you own or administer.",
            )
        }

        error?.let {
            Spacer(Modifier.height(8.dp))
            Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
        }

        Spacer(Modifier.height(16.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(
                enabled = canRun,
                onClick = {
                    error = startRun(context, viewModel, toolId, values)
                },
            ) { Text("Run") }
            if (thisRunning) {
                OutlinedButton(onClick = { ToolRunManager.requestStop() }) { Text("Stop") }
            }
            OutlinedButton(onClick = { navController.navigate("log") }) { Text("Log") }
        }

        if (runState.status == ToolRunManager.Status.DONE && runState.toolId == toolId) {
            Spacer(Modifier.height(12.dp))
            val result = runState.lastResult
            Text(
                "Last run: ${result?.message ?: "finished"}",
                style = MaterialTheme.typography.bodyMedium,
                color = if ((result?.code ?: 0) == 0) {
                    MaterialTheme.colorScheme.primary
                } else {
                    MaterialTheme.colorScheme.secondary
                },
            )
        }
        Spacer(Modifier.height(24.dp))
    }
}

private fun startRun(
    context: Context,
    viewModel: AppViewModel,
    toolId: String,
    values: Map<String, String>,
): String? = try {
    ToolRunManager.start(toolId, viewModel.config, values, viewModel.whitelistEntries)
    // Foreground service only keeps the process alive and shows progress.
    ContextCompat.startForegroundService(context, Intent(context, RunService::class.java))
    null
} catch (t: Throwable) {
    t.message ?: "Could not start the run."
}

@Composable
private fun FieldInput(field: FieldSpec, value: String, onChange: (String) -> Unit) {
    val help = field.help.ifBlank { null }
    when (field.kind) {
        FieldKind.TEXT -> LabeledTextField(field.label, value, onChange, help)
        FieldKind.SECRET -> LabeledTextField(
            field.label, value, onChange, help,
            visualTransformation = PasswordVisualTransformation(),
        )
        FieldKind.MULTILINE -> LabeledTextField(
            field.label, value, onChange, help, singleLine = false, minLines = 3,
        )
        FieldKind.INT -> LabeledTextField(
            field.label, value, onChange, help, keyboardType = KeyboardType.Number,
        )
        FieldKind.DECIMAL -> LabeledTextField(
            field.label, value, onChange, help, keyboardType = KeyboardType.Decimal,
        )
        FieldKind.TOGGLE -> ToggleRow(
            label = field.label,
            checked = value.equals("true", ignoreCase = true),
            onCheckedChange = { onChange(it.toString()) },
            help = help,
        )
        FieldKind.CHOICE -> {
            Column(Modifier.padding(bottom = 10.dp)) {
                Text(field.label, style = MaterialTheme.typography.bodyMedium)
                if (help != null) {
                    Text(
                        help,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.height(6.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    field.choices.forEach { choice ->
                        if (choice == value) {
                            Button(onClick = { onChange(choice) }) { Text(choice) }
                        } else {
                            OutlinedButton(onClick = { onChange(choice) }) { Text(choice) }
                        }
                    }
                }
            }
        }
    }
}
