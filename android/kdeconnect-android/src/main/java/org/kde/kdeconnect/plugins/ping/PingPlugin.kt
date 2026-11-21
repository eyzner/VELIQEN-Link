/*
 * SPDX-FileCopyrightText: 2014 Albert Vaca Cintora <albertvaka@gmail.com>
 *
 * SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
 */
package org.kde.kdeconnect.plugins.ping

import android.Manifest
import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.core.content.getSystemService
import org.kde.kdeconnect.NetworkPacket
import org.kde.kdeconnect.helpers.NotificationHelper
import org.kde.kdeconnect.plugins.Plugin
import org.kde.kdeconnect.plugins.PluginFactory.LoadablePlugin
import org.kde.kdeconnect.ui.MainActivity
import org.kde.kdeconnect.veliqen.screen.ScreenShareActivity
import org.kde.kdeconnect.veliqen.screen.ScreenShareService
import org.kde.kdeconnect_tp.R

@LoadablePlugin
class PingPlugin : Plugin() {
    private var lastScreenRequest = 0L
    override val displayName: String
        get() = context.resources.getString(R.string.pref_plugin_ping)

    override val description: String
        get() = context.resources.getString(R.string.pref_plugin_ping_desc)

    override fun onPacketReceived(np: NetworkPacket): Boolean {
        if (np.type != PACKET_TYPE_PING) {
            Log.e(TAG, "Ping plugin should not receive packets other than pings!")
            return false
        }

        if (np.has("message") && np.getString("message") == ScreenShareService.REQUEST_MESSAGE) {
            if (!device.isPaired) return false
            val now = SystemClock.elapsedRealtime()
            if (lastScreenRequest != 0L && now - lastScreenRequest < 15000) return true
            lastScreenRequest = now
            val launch = Intent(context, ScreenShareActivity::class.java).putExtra("deviceId", device.deviceId)
            val request = PendingIntent.getActivity(context, device.deviceId.hashCode(), launch,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(context, "${device.name} просит показать экран. Открой VELIQEN Link и нажми «Показать экран».", Toast.LENGTH_LONG).show()
                }
                return true
            }
            val notice = NotificationCompat.Builder(context, NotificationHelper.Channels.DEFAULT)
                .setSmallIcon(R.drawable.ic_notification)
                .setContentTitle("Показать экран на ${device.name}?")
                .setContentText("Нажми, чтобы выбрать просмотр или управление и подтвердить показ.")
                .setContentIntent(request).setAutoCancel(true)
                .addAction(0, "Показать экран", request)
                .build()
            context.getSystemService<NotificationManager>()?.notify(device.deviceId.hashCode(), notice)
            return true
        }

        val mutableUpdateFlags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val resultPendingIntent = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java), mutableUpdateFlags)

        val (id: Int, message: String) = if (np.has("message")) {
            val id = SystemClock.elapsedRealtime().toInt()
            Pair(id, np.getString("message"))
        } else {
            val id = 42 // A unique id to create only one notification
            Pair(id, "Ping!")
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            val permissionResult = ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
            if (permissionResult != PackageManager.PERMISSION_GRANTED) {
                // If notifications are not allowed, show a toast instead of a notification
                Handler(Looper.getMainLooper()).post {
                    Toast.makeText(context, message, Toast.LENGTH_LONG).show()
                }
                return true
            }
        }

        val notificationManager = context.getSystemService<NotificationManager>()!!

        val notification = NotificationCompat.Builder(context, NotificationHelper.Channels.DEFAULT)
            .setContentTitle(device.name)
            .setContentText(message)
            .setContentIntent(resultPendingIntent)
            .setTicker(message)
            .setSmallIcon(R.drawable.ic_notification)
            .setAutoCancel(true)
            .setDefaults(Notification.DEFAULT_ALL)
            .setStyle(NotificationCompat.BigTextStyle().bigText(message))
            .build()

        notificationManager.notify(id, notification)

        return true
    }

    override fun getUiMenuEntries(): List<PluginUiMenuEntry> = listOf(
        PluginUiMenuEntry(context.getString(R.string.send_ping)) {
            if (isDeviceInitialized) {
                device.sendPacket(NetworkPacket(PACKET_TYPE_PING))
            }
        }
    )

    override val supportedPacketTypes: Array<String> = arrayOf(PACKET_TYPE_PING)

    override val outgoingPacketTypes: Array<String> = arrayOf(PACKET_TYPE_PING)

    companion object {
        private const val PACKET_TYPE_PING = "kdeconnect.ping"
        private const val TAG = "PingPlugin"
    }
}
