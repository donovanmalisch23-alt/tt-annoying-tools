package com.teamtalk.annoying.ui.screens

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.navigation.NavController
import com.teamtalk.annoying.core.LogBus
import com.teamtalk.annoying.core.Sdk
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.components.Badge
import com.teamtalk.annoying.ui.components.HeroHeader
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.LabeledTextField
import com.teamtalk.annoying.ui.components.SectionTitle

/**
 * Everything that changes how the app behaves lives here, behind a
 * device-local administrator credential: the target server, the exact-host
 * allowlist and the SDK license decision.
 */
@Composable
fun AdminScreen(viewModel: AppViewModel, navController: NavController) {
    val unlocked = viewModel.adminUnlocked

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState()),
    ) {
        HeroHeader(
            title = "Admin panel",
            subtitle = if (unlocked) {
                "You are signed in on this device. Lock the panel again when you are done."
            } else {
                "Manage the server allowlist, the target server and the SDK license for " +
                    "this device."
            },
            chips = if (unlocked) {
                listOf("unlocked", viewModel.adminUser ?: "administrator")
            } else {
                listOf("locked")
            },
        )

        Column(Modifier.padding(16.dp)) {
            if (!unlocked) {
                if (viewModel.adminConfigured) {
                    SignInCard(viewModel)
                } else {
                    ProvisionCard(viewModel)
                }
                Spacer(Modifier.height(14.dp))
                InfoCard(
                    title = "What this protects",
                    body = "The allowlist is the gate that stops every bulk tool from " +
                        "connecting anywhere it was not approved for. Keeping it behind a " +
                        "credential means only the administrator can widen it.",
                )
                InfoCard(
                    title = "Where the credential lives",
                    body = "Only a PBKDF2 hash and a random salt are stored, in this app's " +
                        "private storage on this device. Nothing is uploaded, and a restart " +
                        "locks the panel again.",
                )
            } else {
                UnlockedHeader(viewModel)
                AdminServerSection(viewModel)
                AdminAllowlistSection(viewModel)
                LicenseSection(viewModel)
                DangerSection(viewModel)
            }

            Spacer(Modifier.height(24.dp))
            TextButton(onClick = { navController.navigate("home") }) { Text("Back to tools") }
            Spacer(Modifier.height(20.dp))
        }
    }
}

@Composable
private fun UnlockedHeader(viewModel: AppViewModel) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Badge("signed in", MaterialTheme.colorScheme.primary)
        Text(
            text = viewModel.adminUser ?: "administrator",
            style = MaterialTheme.typography.titleSmall,
            fontWeight = FontWeight.SemiBold,
        )
        Spacer(Modifier.weight(1f))
        OutlinedButton(onClick = { viewModel.adminLock() }) { Text("Lock") }
    }
}

@Composable
private fun ProvisionCard(viewModel: AppViewModel) {
    var username by remember { mutableStateOf("") }
    var password by remember { mutableStateOf("") }
    var confirm by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    SectionTitle("First run")
    Text(
        text = "Set the administrator login for this device. It is local to the app and " +
            "can be cleared again from the bottom of this panel.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(12.dp))
    LabeledTextField("Administrator name", username, { username = it; error = null })
    LabeledTextField(
        "Password", password, { password = it; error = null },
        help = "At least 6 characters.",
        visualTransformation = PasswordVisualTransformation(),
    )
    LabeledTextField(
        "Repeat password", confirm, { confirm = it; error = null },
        visualTransformation = PasswordVisualTransformation(),
    )
    error?.let {
        Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(8.dp))
    }
    Button(onClick = { error = viewModel.adminProvision(username, password, confirm) }) {
        Text("Create administrator")
    }
}

@Composable
private fun SignInCard(viewModel: AppViewModel) {
    var username by remember { mutableStateOf(viewModel.storedAdminName ?: "") }
    var password by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    SectionTitle("Administrator sign-in")
    Text(
        text = "The allowlist, the target server and the license can only be changed by " +
            "the administrator.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(12.dp))
    LabeledTextField("Administrator name", username, { username = it; error = null })
    LabeledTextField(
        "Password", password, { password = it; error = null },
        visualTransformation = PasswordVisualTransformation(),
    )
    error?.let {
        Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
        Spacer(Modifier.height(8.dp))
    }
    Button(onClick = { error = viewModel.adminSignIn(username, password) }) { Text("Unlock panel") }
}

@Composable
private fun LicenseSection(viewModel: AppViewModel) {
    SectionTitle("TeamTalk 5 SDK license")
    InfoCard(
        title = if (viewModel.licenseAccepted) "Accepted on this device" else "Not accepted yet",
        body = "The SDK is linked into this build, so its license must be accepted before " +
            "any tool may connect. Builds without a purchased registration key run in the " +
            "SDK's trial mode.",
        accent = if (viewModel.licenseAccepted) {
            MaterialTheme.colorScheme.primary
        } else {
            MaterialTheme.colorScheme.secondary
        },
    )
    Text(
        text = Sdk.statusLine(),
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(top = 6.dp),
    )
    Spacer(Modifier.height(10.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        if (!viewModel.licenseAccepted) {
            Button(onClick = { viewModel.acceptLicense() }) { Text("I accept the SDK license") }
        } else {
            OutlinedButton(onClick = { viewModel.declineLicense() }) { Text("Withdraw acceptance") }
        }
    }
}

@Composable
private fun DangerSection(viewModel: AppViewModel) {
    var confirmingReset by remember { mutableStateOf(false) }

    SectionTitle("Reset")
    OutlinedButton(onClick = { LogBus.clear() }) { Text("Clear the run log") }
    Spacer(Modifier.height(8.dp))
    OutlinedButton(onClick = { viewModel.resetWhitelist() }) { Text("Reset the allowlist") }
    Spacer(Modifier.height(8.dp))
    OutlinedButton(onClick = { confirmingReset = true }) { Text("Forget the administrator") }

    if (confirmingReset) {
        AlertDialog(
            onDismissRequest = { confirmingReset = false },
            title = { Text("Forget the administrator?") },
            text = {
                Text(
                    "The stored credential is deleted and the panel locks. The next visit " +
                        "asks for a new administrator name and password. The allowlist and " +
                        "the target settings stay as they are.",
                )
            },
            confirmButton = {
                TextButton(
                    onClick = {
                        viewModel.adminForgetCredential()
                        confirmingReset = false
                    },
                ) { Text("Forget it") }
            },
            dismissButton = {
                TextButton(onClick = { confirmingReset = false }) { Text("Cancel") }
            },
        )
    }
}
