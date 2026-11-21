/*
 * SPDX-FileCopyrightText: 2026 VELIQEN contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */
package org.kde.kdeconnect.ui

import android.Manifest
import android.app.Activity
import android.bluetooth.BluetoothAdapter
import android.bluetooth.BluetoothManager
import android.content.ActivityNotFoundException
import android.content.ClipboardManager
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.content.getSystemService
import org.kde.kdeconnect.BackgroundService
import org.kde.kdeconnect.Device
import org.kde.kdeconnect.KdeConnect
import org.kde.kdeconnect.PairingHandler
import org.kde.kdeconnect.helpers.DeviceHelper
import org.kde.kdeconnect.plugins.clipboard.ClipboardPlugin
import org.kde.kdeconnect.plugins.notifications.NotificationReceiver
import org.kde.kdeconnect.plugins.share.SendFileActivity
import org.kde.kdeconnect.plugins.share.SharePlugin
import org.kde.kdeconnect.veliqen.screen.ScreenShareActivity
import org.kde.kdeconnect.veliqen.screen.ScreenShareService
import org.kde.kdeconnect_tp.BuildConfig
import org.kde.kdeconnect_tp.R

/** A deliberately small front door. All devices and permissions come from the real service. */
class VeliqenHomeActivity : AppCompatActivity() {
    private data class Computer(
        val id: String,
        val name: String,
        val reachable: Boolean,
        val paired: Boolean,
        val pairState: PairingHandler.PairState,
        val verification: String?,
        val filesReady: Boolean,
        val clipboardReady: Boolean,
    )

    private val prefs by lazy { getSharedPreferences("veliqen_home", MODE_PRIVATE) }
    private val callbackKey = "VELIQEN_HOME:${System.identityHashCode(this)}"
    private val mainHandler = Handler(Looper.getMainLooper())
    private val watchedDevices = mutableMapOf<String, Device>()
    private var computers by mutableStateOf<List<Computer>>(emptyList())
    private var selectedId by mutableStateOf<String?>(null)
    private var tab by mutableIntStateOf(0)
    private var pageHistory by mutableStateOf<List<Int>>(emptyList())
    private var stateRevision by mutableIntStateOf(0)
    private var message by mutableStateOf<String?>(null)
    private var lastScreenErrorSeen: String? = null
    private var noticeSettingsOpened = false
    private var noticeAccessHelp by mutableStateOf(false)
    private var showAccessChoice by mutableStateOf(false)
    private var showBluetoothHelp by mutableStateOf(false)
    private var showDisconnect by mutableStateOf(false)
    private var showClipboardHelp by mutableStateOf(false)
    private var enableMessages by mutableStateOf(true)
    private var enableContacts by mutableStateOf(true)
    private var enableCalls by mutableStateOf(true)
    private var enableNotices by mutableStateOf(true)
    private var afterPermissions: (() -> Unit)? = null

    private val pairingCallback = object : PairingHandler.PairingCallback {
        override fun incomingPairRequest() = updateOnMain()
        override fun pairingSuccessful() = updateOnMain()
        override fun unpaired(device: Device) = updateOnMain()
        override fun pairingFailed(error: String) {
            runOnUiThread {
                message = error
                updateState()
            }
        }
    }

    private val tick = object : Runnable {
        override fun run() {
            updateState()
            mainHandler.postDelayed(this, 2000)
        }
    }

    private val permissionRequest = registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { results ->
        KdeConnect.getInstance().reloadAllDevicePlugins()
        if (results.any { it.value }) BackgroundService.ForceRefreshConnections(this)
        updateState()
        val next = afterPermissions
        afterPermissions = null
        if (results.values.all { it }) {
            next?.invoke()
        } else {
            message = "Часть доступов не выдана. Их можно включить позже в настройках доступа."
        }
    }

    private val bluetoothRequest = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        message = if (result.resultCode != Activity.RESULT_CANCELED) {
            "Телефон виден для подключения. На Ubuntu выбери его в настройке Bluetooth и проверь код на обоих экранах."
        } else {
            "Подключение Bluetooth отменено. Попробуй снова кнопкой «Звонки»."
        }
        updateState()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        selectedId = savedInstanceState?.getString("selected") ?: prefs.getString("selected", null)
        enableMessages = prefs.getBoolean("want_messages", true)
        enableContacts = prefs.getBoolean("want_contacts", true)
        enableCalls = prefs.getBoolean("want_calls", true)
        enableNotices = prefs.getBoolean("want_notices", true)
        tab = savedInstanceState?.getInt("tab", 0) ?: 0
        pageHistory = savedInstanceState?.getIntArray("page_history")?.toList() ?: if (tab == 0) emptyList() else listOf(0)
        showAccessChoice = savedInstanceState?.getBoolean("access_dialog") ?: false
        showBluetoothHelp = savedInstanceState?.getBoolean("bluetooth_dialog") ?: false
        intent.getStringExtra(MainActivity.EXTRA_DEVICE_ID)?.let(::selectComputer)
        BackgroundService.Start(this)
        updateState()
        setContent { VeliqenTheme { Home() } }
    }

    override fun onStart() {
        super.onStart()
        KdeConnect.getInstance().addDeviceListChangedCallback(callbackKey) { updateOnMain() }
        mainHandler.post(tick)
    }

    override fun onResume() {
        super.onResume()
        if (noticeSettingsOpened) {
            noticeSettingsOpened = false
            noticeAccessHelp = !NotificationReceiver.hasReadNotificationsPermission(this@VeliqenHomeActivity)
        }
        KdeConnect.getInstance().reloadAllDevicePlugins()
        updateState()
    }

    override fun onStop() {
        mainHandler.removeCallbacks(tick)
        KdeConnect.getInstance().removeDeviceListChangedCallback(callbackKey)
        watchedDevices.values.forEach { it.removePairingCallback(pairingCallback) }
        watchedDevices.clear()
        super.onStop()
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        // The exported launcher may select a device, but never accepts a pairing from intent extras.
        intent.getStringExtra(MainActivity.EXTRA_DEVICE_ID)?.let(::selectComputer)
        tab = 0
        pageHistory = emptyList()
        updateState()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putString("selected", selectedId)
        outState.putInt("tab", tab)
        outState.putIntArray("page_history", pageHistory.toIntArray())
        outState.putBoolean("access_dialog", showAccessChoice)
        outState.putBoolean("bluetooth_dialog", showBluetoothHelp)
        super.onSaveInstanceState(outState)
    }

    private fun updateOnMain() { runOnUiThread { updateState() } }

    private fun updateState() {
        val screenError = ScreenShareService.lastError
        if (screenError == null) lastScreenErrorSeen = null
        else if (screenError != lastScreenErrorSeen) {
            message = screenError
            lastScreenErrorSeen = screenError
        }
        val devices = KdeConnect.getInstance().devices.values.toList()
        for ((id, old) in watchedDevices.toMap()) {
            if (devices.none { it.deviceId == id }) {
                old.removePairingCallback(pairingCallback)
                watchedDevices.remove(id)
            }
        }
        if (lifecycle.currentState.isAtLeast(androidx.lifecycle.Lifecycle.State.STARTED)) {
            devices.forEach { device ->
                if (watchedDevices.putIfAbsent(device.deviceId, device) == null) device.addPairingCallback(pairingCallback)
            }
        }
        computers = devices.sortedWith(compareByDescending<Device> { it.isPaired }.thenBy { it.name.lowercase() }).map {
            Computer(it.deviceId, it.name, it.isReachable, it.isPaired, it.pairStatus, it.verificationKey,
                it.getPlugin(SharePlugin::class.java) != null, it.getPlugin(ClipboardPlugin::class.java) != null)
        }
        if (selectedId == null) computers.filter { it.paired }.singleOrNull()?.let { selectComputer(it.id) }
        stateRevision++
    }

    private fun selectComputer(id: String) {
        selectedId = id
        prefs.edit().putString("selected", id).apply()
    }

    private fun currentComputer(): Computer? = computers.firstOrNull { it.id == selectedId && it.paired }

    private fun currentDevice(): Device? {
        val device = KdeConnect.getInstance().getDevice(selectedId)
        return if (device?.isPaired == true && device.isReachable) device else {
            message = "Сначала подключи компьютер. Он должен быть включён и находиться в одной сети с телефоном."
            null
        }
    }

    private fun has(permission: String) = ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED
    private fun basePermissions(): List<String> = buildList {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.CINNAMON_BUN) add(Manifest.permission.ACCESS_LOCAL_NETWORK)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) add(Manifest.permission.POST_NOTIFICATIONS)
    }
    private fun networkReady() = Build.VERSION.SDK_INT < Build.VERSION_CODES.CINNAMON_BUN || has(Manifest.permission.ACCESS_LOCAL_NETWORK)
    private fun smsPermissions() = listOf(Manifest.permission.READ_SMS, Manifest.permission.SEND_SMS, Manifest.permission.READ_PHONE_STATE)
    private fun callPermissions() = buildList {
        add(Manifest.permission.READ_PHONE_STATE)
        add(Manifest.permission.READ_CALL_LOG)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            add(Manifest.permission.BLUETOOTH_CONNECT)
            add(Manifest.permission.BLUETOOTH_ADVERTISE)
        }
    }
    private fun backgroundReady() = getSystemService<PowerManager>()?.isIgnoringBatteryOptimizations(packageName) == true

    private fun requestPermissions(permissions: List<String>, after: (() -> Unit)? = null) {
        val missing = permissions.distinct().filterNot(::has)
        if (missing.isEmpty()) { after?.invoke(); updateState(); return }
        val blocked = missing.filter { prefs.getBoolean("asked:$it", false) && !ActivityCompat.shouldShowRequestPermissionRationale(this, it) }
        val askable = missing - blocked.toSet()
        if (askable.isEmpty()) {
            message = "Android больше не показывает запрос. Открой «Разрешения» на следующем экране и включи нужный доступ."
            openAppSettings()
            return
        }
        afterPermissions = if (blocked.isEmpty()) after else null
        prefs.edit().also { editor -> askable.forEach { editor.putBoolean("asked:$it", true) } }.apply()
        permissionRequest.launch(askable.toTypedArray())
    }

    private fun beginSearch() {
        val networkPermission = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.CINNAMON_BUN) listOf(Manifest.permission.ACCESS_LOCAL_NETWORK) else emptyList()
        // Notification permission is useful, but denying it must never block a foreground pairing.
        requestPermissions(networkPermission) {
            BackgroundService.ForceRefreshConnections(this)
            message = "Поиск обновлён. Открой Link на Ubuntu в той же сети Wi-Fi."
            updateState()
        }
    }

    private fun requestPair(id: String) {
        val device = KdeConnect.getInstance().getDevice(id) ?: return
        if (!device.isReachable || device.isPaired) return
        selectComputer(id)
        device.requestPairing()
        updateState()
    }

    private fun acceptPair(id: String) {
        val device = KdeConnect.getInstance().getDevice(id) ?: return
        if (device.pairStatus != PairingHandler.PairState.RequestedByPeer) { updateState(); return }
        selectComputer(id)
        device.acceptPairing()
        updateState()
    }

    private fun cancelPair(id: String) {
        KdeConnect.getInstance().getDevice(id)?.cancelPairing()
        updateState()
    }

    private fun sendFile() {
        val device = currentDevice() ?: return
        if (device.getPlugin(SharePlugin::class.java) == null) { message = "Передача файлов отключена. Включи её в дополнительных настройках компьютера."; return }
        launch(Intent(this, SendFileActivity::class.java).putExtra(SendFileActivity.EXTRA_DEVICE_ID, device.deviceId))
    }

    private fun sendClipboard() {
        val device = currentDevice() ?: return
        val plugin = device.getPlugin(ClipboardPlugin::class.java)
        if (plugin == null) { message = "Буфер отключён. Включи его в дополнительных настройках компьютера."; return }
        val clip = getSystemService<ClipboardManager>()?.primaryClip
        val content = clip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(this)?.toString()
        if (content.isNullOrEmpty()) { message = "В буфере пока нет текста. Скопируй текст и нажми эту кнопку ещё раз."; return }
        plugin.propagateClipboard(content)
        message = "Отправляем текст на ${device.name}."
    }

    private fun shareScreen() {
        val device = currentDevice() ?: return
        message = null
        launch(Intent(this, ScreenShareActivity::class.java).putExtra("deviceId", device.deviceId))
    }

    private fun prepareBluetooth() {
        val permissions = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) listOf(Manifest.permission.BLUETOOTH_CONNECT, Manifest.permission.BLUETOOTH_ADVERTISE) else emptyList()
        requestPermissions(permissions) {
            val adapter = getSystemService<BluetoothManager>()?.adapter
            if (adapter == null) { message = "На этом устройстве нет Bluetooth."; return@requestPermissions }
            try {
                bluetoothRequest.launch(Intent(BluetoothAdapter.ACTION_REQUEST_DISCOVERABLE).putExtra(BluetoothAdapter.EXTRA_DISCOVERABLE_DURATION, 180))
            } catch (_: Exception) {
                launch(Intent(Settings.ACTION_BLUETOOTH_SETTINGS))
            }
        }
    }

    private fun bluetoothDescription(): String {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S && !has(Manifest.permission.BLUETOOTH_CONNECT)) return "Нужно разрешить Bluetooth"
        return try {
            val adapter = getSystemService<BluetoothManager>()?.adapter
            when {
                adapter == null -> "Bluetooth недоступен на этом устройстве"
                !adapter.isEnabled -> "Bluetooth выключен"
                else -> "Телефон в Bluetooth: ${adapter.name ?: DeviceHelper.getDeviceName(this)}"
            }
        } catch (_: SecurityException) { "Нужно разрешить Bluetooth" }
    }

    private fun notificationAccess() {
        noticeSettingsOpened = true
        val component = ComponentName(this, NotificationReceiver::class.java)
        val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            Intent(Settings.ACTION_NOTIFICATION_LISTENER_DETAIL_SETTINGS).putExtra(Settings.EXTRA_NOTIFICATION_LISTENER_COMPONENT_NAME, component.flattenToString())
        } else Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)
        launch(intent, Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
    }

    private fun backgroundAccess() {
        launch(Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:$packageName")),
            Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
    }

    private fun openAppSettings() = launch(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))

    private fun launch(intent: Intent, fallback: Intent? = null) {
        try { startActivity(intent) }
        catch (_: ActivityNotFoundException) {
            if (fallback != null) launch(fallback) else message = "Этот экран недоступен в прошивке. Открой настройки Android вручную."
        } catch (_: SecurityException) {
            message = "Android не разрешил открыть этот экран. Проверь доступы приложения в настройках."
        }
    }

    private fun openAdvanced() {
        val target = Intent(this, MainActivity::class.java).putExtra("veliqen_advanced", true)
        selectedId?.let { target.putExtra(MainActivity.EXTRA_DEVICE_ID, it) }
        launch(target)
    }

    private fun navigateTo(destination: Int) {
        if (destination == tab) return
        pageHistory = pageHistory + tab
        tab = destination
    }

    private fun navigateBack() {
        tab = pageHistory.lastOrNull() ?: 0
        pageHistory = pageHistory.dropLast(1)
    }

    private fun goHome() {
        tab = 0
        pageHistory = emptyList()
    }

    private fun setupAccess() {
        navigateTo(1)
        showAccessChoice = true
    }

    @Composable
    private fun Home() {
        @Suppress("UNUSED_VARIABLE") val revision = stateRevision
        BackHandler(enabled = tab != 0) { navigateBack() }
        Scaffold(containerColor = MaterialTheme.colorScheme.background) { insets ->
            Box(Modifier.fillMaxSize().padding(insets), contentAlignment = Alignment.TopCenter) {
                Column(Modifier.widthIn(max = 760.dp).fillMaxSize()) {
                    Header()
                    key(tab) {
                        Column(
                            Modifier.fillMaxWidth().weight(1f).verticalScroll(rememberScrollState())
                                .padding(horizontal = 16.dp).padding(top = 4.dp, bottom = 20.dp),
                            verticalArrangement = Arrangement.spacedBy(16.dp),
                        ) {
                            message?.let { text ->
                                Surface(shape = RoundedCornerShape(14.dp), color = MaterialTheme.colorScheme.secondaryContainer) {
                                    Row(Modifier.padding(start = 14.dp, top = 4.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                                        Text(text, modifier = Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                                        IconButton(onClick = { message = null }) { Icon(Icons.Default.Close, "Закрыть сообщение", modifier = Modifier.size(20.dp)) }
                                    }
                                }
                            }
                            when (tab) { 0 -> ConnectionPage(); 1 -> AccessPage(); else -> MorePage() }
                        }
                    }
                }
            }
        }
        if (showAccessChoice) AccessChoiceDialog()
        if (showBluetoothHelp) BluetoothDialog()
        if (showClipboardHelp) AlertDialog(
            onDismissRequest = { showClipboardHelp = false }, title = { Text("Буфер обмена") },
            text = { Text(if (ClipboardPlugin.canSyncAutomatically(this@VeliqenHomeActivity))
                "Прошивка разрешает фоновый буфер. Скопированный текст может передаваться подключённым компьютерам."
                else "Скопируй текст, открой Link и нажми «Буфер». Android разрешает приложению читать буфер, пока оно открыто. Автоматическая передача в фоне доступна системной версии VELIQEN OS.") },
            confirmButton = { TextButton(onClick = { showClipboardHelp = false }) { Text("Понятно") } },
        )
        if (showDisconnect) AlertDialog(
            onDismissRequest = { showDisconnect = false }, title = { Text("Удалить сопряжение?") },
            text = { Text("${currentComputer()?.name ?: "Компьютер"} потеряет доступ к телефону. Для нового подключения потребуется подтверждение.") },
            confirmButton = { TextButton(onClick = {
                KdeConnect.getInstance().getDevice(selectedId)?.unpair()
                selectedId = null
                prefs.edit().remove("selected").apply()
                showDisconnect = false
                goHome()
                updateState()
            }) { Text("Удалить") } }, dismissButton = { TextButton(onClick = { showDisconnect = false }) { Text("Отмена") } },
        )
    }

    @Composable
    private fun Header() {
        Row(Modifier.fillMaxWidth().heightIn(min = 60.dp).padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            if (tab != 0) IconButton(onClick = ::navigateBack) { Icon(Icons.Default.ArrowBack, "Назад") }
            Text(
                when (tab) { 0 -> "VELIQEN Link"; 1 -> "Доступы"; else -> "Настройки" },
                modifier = Modifier.weight(1f).padding(start = if (tab == 0) 8.dp else 0.dp),
                style = MaterialTheme.typography.titleLarge, fontSize = 21.sp, fontWeight = FontWeight.SemiBold,
            )
            if (tab == 0) IconButton(onClick = { navigateTo(2) }) {
                Icon(Icons.Default.Settings, "Настройки")
            }
        }
    }

    @Composable
    private fun ConnectionPage() {
        val selected = currentComputer()
        val pending = computers.filter { it.pairState == PairingHandler.PairState.RequestedByPeer || it.pairState == PairingHandler.PairState.Requested }
        val streamingId = ScreenShareService.activeDeviceId
        pending.forEach { PairCard(it) }
        if (streamingId != null && (selected == null || streamingId != selected.id)) {
            Group {
                SettingsRow("Экран передаётся", computers.firstOrNull { it.id == streamingId }?.name ?: "Подключённый компьютер",
                    trailing = { Text("Стоп", color = MaterialTheme.colorScheme.primary, fontWeight = FontWeight.SemiBold) }) {
                    ScreenShareService.stop(this@VeliqenHomeActivity)
                    updateState()
                }
            }
        }
        if (selected != null) {
            BoxWithConstraints(Modifier.fillMaxWidth()) {
                if (maxWidth >= 600.dp) {
                    Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.Top) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                            ComputerPanel(selected)
                            AccessSummary()
                        }
                        QuickActions(selected, streamingId, Modifier.weight(1f))
                    }
                } else {
                    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                        ComputerPanel(selected)
                        QuickActions(selected, streamingId)
                        AccessSummary()
                    }
                }
            }
        } else if (pending.isEmpty()) {
            Panel {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    DeviceIcon()
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        Text("Подключить компьютер", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                        Body("Открой Link на Ubuntu в той же сети Wi-Fi.")
                    }
                }
                Action(if (networkReady()) "Найти компьютер" else "Разрешить подключение", ::beginSearch)
            }
        }

        val others = computers.filter { it.id != selected?.id && it.pairState !in listOf(PairingHandler.PairState.Requested, PairingHandler.PairState.RequestedByPeer) }
        if (others.isNotEmpty()) {
            SectionTitle(if (selected == null) "Компьютеры" else "Другие компьютеры")
            Group {
                others.forEachIndexed { index, computer ->
                    if (index != 0) Divider()
                    SettingsRow(computer.name,
                        if (computer.paired) { if (computer.reachable) "Подключён" else "Сопряжён · нет связи" } else "Нажми для подключения",
                        trailing = { Icon(Icons.Default.KeyboardArrowRight, null) }) {
                        if (computer.paired) { selectComputer(computer.id); goHome() } else requestPair(computer.id)
                    }
                }
            }
        } else if (selected == null && pending.isEmpty()) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("Компьютер пока не найден", Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant)
                TextButton(onClick = { launch(Intent(Settings.ACTION_WIFI_SETTINGS)) }) { Text("Wi-Fi") }
            }
        }
    }

    @Composable
    private fun ComputerPanel(computer: Computer) {
        Panel {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                DeviceIcon()
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text(computer.name, style = MaterialTheme.typography.titleLarge, fontSize = 21.sp,
                        fontWeight = FontWeight.SemiBold, maxLines = 2, overflow = TextOverflow.Ellipsis)
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        Box(Modifier.size(7.dp).background(
                            if (computer.reachable) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.onSurfaceVariant,
                            CircleShape))
                        Text(if (computer.reachable) "Подключён" else "Нет связи", style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                }
                if (!computer.reachable) IconButton(onClick = ::beginSearch) { Icon(Icons.Default.Refresh, "Подключиться снова") }
            }
            if (!computer.reachable) Body("Открой Link на компьютере в той же сети Wi-Fi. Сопряжение сохранено.")
        }
    }

    @Composable
    private fun QuickActions(computer: Computer, streamingId: String?, modifier: Modifier = Modifier) {
        val screenActive = streamingId == computer.id
        Column(modifier.fillMaxWidth(), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                QuickAction("Экран", if (screenActive) "Остановить показ" else "Показать на Ubuntu",
                    Modifier.weight(1f), enabled = screenActive || (computer.reachable && streamingId == null), active = screenActive,
                    icon = { Icon(painterResource(R.drawable.ic_device_laptop_32dp), null, Modifier.size(26.dp)) }) {
                    if (screenActive) { ScreenShareService.stop(this@VeliqenHomeActivity); updateState() } else shareScreen()
                }
                QuickAction("Файлы", if (computer.filesReady || !computer.reachable) "Отправить файл" else "Включить передачу",
                    Modifier.weight(1f), enabled = computer.reachable,
                    icon = { Icon(painterResource(R.drawable.share_plugin_action_24dp), null, Modifier.size(26.dp)) }) {
                    if (computer.filesReady) sendFile() else openAdvanced()
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                QuickAction("Буфер", if (computer.clipboardReady || !computer.reachable) "Передать текст" else "Включить передачу",
                    Modifier.weight(1f), enabled = computer.reachable,
                    icon = { Icon(painterResource(R.drawable.ic_baseline_content_paste_24), null, Modifier.size(26.dp)) }) {
                    if (computer.clipboardReady) sendClipboard() else openAdvanced()
                }
                QuickAction("Звонки", "Подключить Bluetooth", Modifier.weight(1f),
                    icon = { Icon(Icons.Default.Phone, null, Modifier.size(26.dp)) }) { showBluetoothHelp = true }
            }
        }
    }

    @Composable
    private fun AccessSummary() {
        val ready = coreAccessReady()
        Group {
            SettingsRow(if (ready) "Доступы" else "Настроить доступы",
                if (ready) "Выбранные разрешения выданы" else "SMS, контакты и уведомления",
                trailing = { Icon(if (ready) Icons.Default.CheckCircle else Icons.Default.KeyboardArrowRight, null,
                    tint = if (ready) MaterialTheme.colorScheme.tertiary else MaterialTheme.colorScheme.onSurfaceVariant) }) {
                if (ready) navigateTo(1) else setupAccess()
            }
        }
    }

    @Composable
    private fun PairCard(computer: Computer) {
        val incoming = computer.pairState == PairingHandler.PairState.RequestedByPeer
        Panel(accent = true) {
            Text(if (incoming) "Запрос на подключение" else "Подтверди на компьютере", style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.primary)
            Text(computer.name, style = MaterialTheme.typography.titleLarge, fontWeight = FontWeight.SemiBold)
            Body(if (incoming) "Сравни код на обоих экранах. Подключай только свой компьютер." else "Сравни код и прими запрос в Link на Ubuntu.")
            computer.verification?.takeIf { it.isNotBlank() }?.let { code ->
                Text(code, fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.titleMedium,
                    modifier = Modifier.fillMaxWidth().padding(vertical = 4.dp))
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                if (incoming) Button(onClick = { acceptPair(computer.id) }, modifier = Modifier.weight(1f).heightIn(min = 48.dp),
                    shape = RoundedCornerShape(12.dp)) { Text("Подключить") }
                TextButton(onClick = { cancelPair(computer.id) }, modifier = Modifier.heightIn(min = 48.dp)) {
                    Text(if (incoming) "Отклонить" else "Отменить запрос")
                }
            }
        }
    }

    // App notifications are optional; refusing them must not keep setup incomplete.
    private fun coreAccessReady() = networkReady() &&
        (!enableMessages || smsPermissions().all(::has)) &&
        (!enableContacts || has(Manifest.permission.READ_CONTACTS)) &&
        (!enableCalls || callPermissions().all(::has)) &&
        (!enableNotices || NotificationReceiver.hasReadNotificationsPermission(this@VeliqenHomeActivity))

    @Composable
    private fun AccessPage() {
        Action(if (coreAccessReady()) "Выбрать возможности" else "Настроить доступы", { showAccessChoice = true })
        Group {
            AccessRow("Связь с компьютером", "Доступ к локальной сети", networkReady()) {
                val permissions = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.CINNAMON_BUN) listOf(Manifest.permission.ACCESS_LOCAL_NETWORK) else emptyList()
                requestPermissions(permissions)
            }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                Divider()
                AccessRow("Уведомления Link", "Запросы на подключение", has(Manifest.permission.POST_NOTIFICATIONS)) {
                    requestPermissions(listOf(Manifest.permission.POST_NOTIFICATIONS))
                }
            }
        }
        SectionTitle("На компьютере")
        Group {
            AccessRow("SMS", "Чтение и отправка по тарифу SIM", smsPermissions().all(::has)) { requestPermissions(smsPermissions()) }
            Divider()
            AccessRow("Контакты", "Имена вместо номеров", has(Manifest.permission.READ_CONTACTS)) { requestPermissions(listOf(Manifest.permission.READ_CONTACTS)) }
            Divider()
            AccessRow("Звонки", "Состояние звонка и Bluetooth", callPermissions().all(::has)) { requestPermissions(callPermissions()) }
            Divider()
            AccessRow("Уведомления телефона", "Уведомления приложений на Ubuntu", NotificationReceiver.hasReadNotificationsPermission(this@VeliqenHomeActivity), grantedClick = ::notificationAccess) { notificationAccess() }
        }
        if (noticeAccessHelp && !NotificationReceiver.hasReadNotificationsPermission(this@VeliqenHomeActivity)) {
            Panel {
                Text("Android не даёт включить доступ?", style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold)
                Body("Если показана «Ограниченная настройка»: сведения о приложении → меню → «Разрешить ограниченные настройки». Затем снова открой доступ к уведомлениям.")
                TextButton(onClick = ::openAppSettings) { Text("Сведения о приложении") }
            }
        }
        Text("Показ экрана подтверждается отдельно при каждом запуске. Управление с компьютера включается на следующем шаге.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 4.dp))
    }

    @Composable
    private fun MorePage() {
        Group {
            SettingsRow("Этот телефон", DeviceHelper.getDeviceName(this@VeliqenHomeActivity), clicked = ::openAdvanced)
            Divider()
            SettingsRow("Доступы", if (coreAccessReady()) "Выбранные разрешения выданы" else "Есть невыданные разрешения") { navigateTo(1) }
            Divider()
            SettingsRow("Работа в фоне", if (backgroundReady()) "Разрешена" else "Настроить для постоянной связи", clicked = ::backgroundAccess)
            Divider()
            SettingsRow("Bluetooth", bluetoothDescription()) { showBluetoothHelp = true }
            Divider()
            SettingsRow("Буфер обмена", if (ClipboardPlugin.canSyncAutomatically(this@VeliqenHomeActivity)) "Автоматическая передача" else "Передача с главного экрана") { showClipboardHelp = true }
        }
        SectionTitle("Компьютер")
        Group {
            SettingsRow("Подключить другой", "Поиск в локальной сети") { goHome(); beginSearch() }
            currentComputer()?.let { computer ->
                Divider()
                SettingsRow("Удалить сопряжение", computer.name, destructive = true) { showDisconnect = true }
            }
        }
        SectionTitle("Дополнительно")
        Group {
            SettingsRow("Настройки возможностей", "Плагины, имя и список приложений", clicked = ::openAdvanced)
            Divider()
            SettingsRow("Настройки Android", clicked = ::openAppSettings)
        }
        Text("VELIQEN Link ${BuildConfig.VERSION_NAME}\nНа основе KDE Connect. Авторы и лицензии — в дополнительных настройках.",
            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 4.dp))
    }

    @Composable
    private fun AccessChoiceDialog() {
        var messages by rememberSaveable { mutableStateOf(enableMessages) }
        var contacts by rememberSaveable { mutableStateOf(enableContacts) }
        var calls by rememberSaveable { mutableStateOf(enableCalls) }
        var notices by rememberSaveable { mutableStateOf(enableNotices) }
        AlertDialog(onDismissRequest = { showAccessChoice = false }, title = { Text("Что подключить?") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    Text("Выбери нужное. Android запросит недостающие разрешения.", style = MaterialTheme.typography.bodyMedium)
                    Choice("SMS", "Чтение и отправка по тарифу SIM", messages) { messages = it }
                    Choice("Контакты", "Имена вместо номеров", contacts) { contacts = it }
                    Choice("Звонки", "Состояние звонка и Bluetooth", calls) { calls = it }
                    Choice("Уведомления", "Из приложений телефона", notices) { notices = it }
                    Text("Доступ к сети и уведомлениям Link будет запрошен при необходимости. Выданные разрешения меняются в настройках Android.", style = MaterialTheme.typography.bodySmall)
                }
            }, confirmButton = { TextButton(onClick = {
                showAccessChoice = false
                enableMessages = messages
                enableContacts = contacts
                enableCalls = calls
                enableNotices = notices
                prefs.edit().putBoolean("want_messages", enableMessages).putBoolean("want_contacts", enableContacts)
                    .putBoolean("want_calls", enableCalls).putBoolean("want_notices", enableNotices).apply()
                val permissions = buildList {
                    addAll(basePermissions())
                    if (enableMessages) addAll(smsPermissions())
                    if (enableContacts) add(Manifest.permission.READ_CONTACTS)
                    if (enableCalls) addAll(callPermissions())
                }
                requestPermissions(permissions) {
                    if (enableNotices && !NotificationReceiver.hasReadNotificationsPermission(this@VeliqenHomeActivity)) {
                        message = "Включи уведомления для VELIQEN Link на системном экране."
                        notificationAccess()
                    } else message = "Выбранные доступы выданы."
                }
            }) { Text("Продолжить") } }, dismissButton = { TextButton(onClick = { showAccessChoice = false }) { Text("Не сейчас") } },
        )
    }

    @Composable
    private fun BluetoothDialog() {
        AlertDialog(onDismissRequest = { showBluetoothHelp = false }, title = { Text("Подключить звонки") },
            text = {
                Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    Text("1. Сделай телефон видимым кнопкой ниже.")
                    Text("2. На Ubuntu: «Звонки» → «Подключить звонки». Выбери телефон и проверь код на обоих экранах.")
                    Text("3. Разреши звонки для этого Bluetooth-устройства, если Android предложит.")
                    Text(bluetoothDescription(), color = MaterialTheme.colorScheme.primary)
                    Text("Доступность звонков проверяется на Ubuntu. Экран и файлы передаются по Wi-Fi.", style = MaterialTheme.typography.bodySmall)
                }
            }, confirmButton = { TextButton(onClick = { showBluetoothHelp = false; prepareBluetooth() }) { Text("Сделать видимым") } },
            dismissButton = { TextButton(onClick = { showBluetoothHelp = false; launch(Intent(Settings.ACTION_BLUETOOTH_SETTINGS)) }) { Text("Настройки Bluetooth") } },
        )
    }

    @Composable
    private fun Choice(title: String, description: String, checked: Boolean, changed: (Boolean) -> Unit) {
        Row(Modifier.fillMaxWidth().heightIn(min = 64.dp).toggleable(value = checked, role = Role.Checkbox, onValueChange = changed), verticalAlignment = Alignment.CenterVertically) {
            Checkbox(checked = checked, onCheckedChange = null)
            Column(Modifier.weight(1f)) {
                Text(title, style = MaterialTheme.typography.bodyLarge)
                Text(description, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }

    @Composable
    private fun AccessRow(title: String, description: String, granted: Boolean, grantedClick: () -> Unit = ::openAppSettings, clicked: () -> Unit) {
        SettingsRow(title, if (granted) "Разрешено" else description,
            trailing = {
                if (granted) Icon(Icons.Default.Check, "Разрешено", tint = MaterialTheme.colorScheme.tertiary, modifier = Modifier.size(22.dp))
                else Text("Разрешить", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.primary)
            }) { if (granted) grantedClick() else clicked() }
    }

    @Composable
    private fun SettingsRow(
        title: String,
        description: String? = null,
        destructive: Boolean = false,
        trailing: @Composable () -> Unit = { Icon(Icons.Default.KeyboardArrowRight, null, tint = MaterialTheme.colorScheme.onSurfaceVariant) },
        clicked: () -> Unit,
    ) {
        Row(Modifier.fillMaxWidth().heightIn(min = 56.dp).clickable(role = Role.Button, onClick = clicked)
            .padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(title, style = MaterialTheme.typography.bodyLarge, fontWeight = FontWeight.Medium,
                    color = if (destructive) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurface)
                description?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
            }
            trailing()
        }
    }

    @Composable
    private fun QuickAction(
        title: String,
        description: String,
        modifier: Modifier = Modifier,
        enabled: Boolean = true,
        active: Boolean = false,
        icon: @Composable () -> Unit,
        clicked: () -> Unit,
    ) {
        Surface(onClick = clicked, enabled = enabled, modifier = modifier.semantics { role = Role.Button },
            shape = RoundedCornerShape(18.dp),
            color = if (active) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface,
            contentColor = if (enabled) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.45f)) {
            Column(Modifier.fillMaxWidth().heightIn(min = 106.dp).padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
                icon()
                Text(title, style = MaterialTheme.typography.titleSmall, fontWeight = FontWeight.SemiBold,
                    color = if (enabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f))
                Text(description, style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = if (enabled) 1f else 0.6f))
            }
        }
    }

    @Composable
    private fun Group(content: @Composable ColumnScope.() -> Unit) {
        Surface(modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp), color = MaterialTheme.colorScheme.surface) {
            Column(content = content)
        }
    }

    @Composable
    private fun Panel(accent: Boolean = false, content: @Composable ColumnScope.() -> Unit) {
        Surface(modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(18.dp),
            color = if (accent) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surface) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp), content = content)
        }
    }

    @Composable
    private fun Divider() {
        HorizontalDivider(Modifier.padding(start = 16.dp), color = MaterialTheme.colorScheme.outlineVariant.copy(alpha = 0.45f))
    }

    @Composable
    private fun SectionTitle(text: String) {
        Text(text, style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(start = 4.dp))
    }

    @Composable
    private fun DeviceIcon() {
        Box(Modifier.size(48.dp).background(MaterialTheme.colorScheme.primaryContainer, RoundedCornerShape(13.dp)), contentAlignment = Alignment.Center) {
            Icon(painterResource(R.drawable.ic_device_laptop_32dp), contentDescription = null,
                tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(30.dp))
        }
    }

    @Composable
    private fun Action(text: String, clicked: () -> Unit) {
        Button(onClick = clicked, modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp), shape = RoundedCornerShape(12.dp),
            contentPadding = PaddingValues(horizontal = 16.dp, vertical = 11.dp)) { Text(text, fontSize = 16.sp, fontWeight = FontWeight.SemiBold) }
    }

    @Composable
    private fun Body(text: String) { Text(text, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant) }
}

@Composable
internal fun VeliqenTheme(content: @Composable () -> Unit) {
    val colors = if (isSystemInDarkTheme()) darkColorScheme(
        primary = Color(0xFFFF8994), onPrimary = Color(0xFF4D1720), primaryContainer = Color(0xFF402127), onPrimaryContainer = Color(0xFFFFD9DD),
        background = Color(0xFF101113), onBackground = Color(0xFFF5F2F2), surface = Color(0xFF1D1E22), onSurface = Color(0xFFF5F2F2),
        onSurfaceVariant = Color(0xFFBBB8BE), secondaryContainer = Color(0xFF303037), onSecondaryContainer = Color(0xFFF4F0F6),
        tertiary = Color(0xFF8DDBAC), outlineVariant = Color(0xFF48484D),
    ) else lightColorScheme(
        primary = Color(0xFFAF283A), onPrimary = Color.White, primaryContainer = Color(0xFFFFE4E8), onPrimaryContainer = Color(0xFF601322),
        background = Color(0xFFF3F3F5), onBackground = Color(0xFF1F2023), surface = Color.White, onSurface = Color(0xFF1F2023),
        onSurfaceVariant = Color(0xFF65636A), secondaryContainer = Color(0xFFECE9EF), onSecondaryContainer = Color(0xFF343038),
        tertiary = Color(0xFF23734D), outlineVariant = Color(0xFFD2D2D7),
    )
    MaterialTheme(colorScheme = colors, content = content)
}
