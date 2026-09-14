package com.teamtalk.annoying.service

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import com.teamtalk.annoying.R
import com.teamtalk.annoying.run.ToolRunManager
import com.teamtalk.annoying.ui.MainActivity
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Keeps the process in the foreground while a test runs, so Android does not
 * freeze the connections mid-run, and exposes a Stop action in the shade.
 * The service does not own the run itself — [ToolRunManager] does.
 */
class RunService : Service() {

    private val stopping = AtomicBoolean(false)

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            ToolRunManager.requestStop()
            stopSelf()
            return START_NOT_STICKY
        }

        createChannel()
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            buildNotification(),
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC
            } else {
                0
            },
        )
        watchForCompletion()
        return START_NOT_STICKY
    }

    private fun watchForCompletion() {
        Thread({
            // Grace period: the UI may start the service just before or just
            // after the run begins, so wait briefly for it to appear rather
            // than exiting instantly and killing the notification.
            val graceStart = System.currentTimeMillis()
            while (!ToolRunManager.isRunning && System.currentTimeMillis() - graceStart < 3_000) {
                try {
                    Thread.sleep(100)
                } catch (interrupted: InterruptedException) {
                    Thread.currentThread().interrupt()
                    break
                }
            }
            while (ToolRunManager.isRunning) {
                try {
                    Thread.sleep(500)
                } catch (interrupted: InterruptedException) {
                    Thread.currentThread().interrupt()
                    break
                }
            }
            if (stopping.compareAndSet(false, true)) {
                ServiceCompat.stopForeground(this, ServiceCompat.STOP_FOREGROUND_REMOVE)
                stopSelf()
            }
        }, "run-service-watch").apply { isDaemon = true }.start()
    }

    private fun createChannel() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val manager = getSystemService(NotificationManager::class.java) ?: return
        val channel = NotificationChannel(
            CHANNEL_ID,
            getString(R.string.run_channel_name),
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = getString(R.string.run_channel_description)
        }
        manager.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        val openIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val stopIntent = PendingIntent.getService(
            this,
            1,
            Intent(this, RunService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val title = ToolRunManager.currentToolTitle.ifBlank {
            getString(R.string.run_notification_title)
        }
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setContentTitle(getString(R.string.run_notification_title))
            .setContentText(title)
            .setSmallIcon(R.drawable.ic_launcher_foreground)
            .setOngoing(true)
            .setContentIntent(openIntent)
            .addAction(0, getString(R.string.run_notification_stop), stopIntent)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .build()
    }

    companion object {
        const val ACTION_STOP = "com.teamtalk.annoying.action.STOP"
        private const val CHANNEL_ID = "tt_run"
        private const val NOTIFICATION_ID = 42
    }
}
