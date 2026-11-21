// SPDX-License-Identifier: GPL-3.0-or-later
package org.kde.kdeconnect.veliqen.screen

import android.accessibilityservice.AccessibilityService
import android.app.Activity
import android.app.KeyguardManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.PixelFormat
import android.hardware.display.DisplayManager
import android.hardware.display.VirtualDisplay
import android.media.ImageReader
import android.media.projection.MediaProjection
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Looper
import android.os.SystemClock
import android.util.DisplayMetrics
import android.view.WindowManager
import android.widget.Toast
import androidx.core.app.NotificationCompat
import org.json.JSONObject
import org.kde.kdeconnect.Device
import org.kde.kdeconnect.KdeConnect
import org.kde.kdeconnect.helpers.DeviceHelper
import org.kde.kdeconnect.helpers.security.SslHelper
import org.kde.kdeconnect_tp.R
import java.io.BufferedInputStream
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.DataInputStream
import java.io.DataOutputStream
import java.io.IOException
import java.net.InetSocketAddress
import java.net.Socket
import java.security.MessageDigest
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import javax.net.ssl.SSLSocket
import kotlin.concurrent.thread
import kotlin.math.min
import kotlin.math.roundToInt

/** One visible, foreground-only MediaProjection session, bound to one paired PC. */
class ScreenShareService : Service() {
    private val main = Handler(Looper.getMainLooper())
    private val stopped = AtomicBoolean(false)
    private val frames = ArrayBlockingQueue<ByteArray>(1)
    private val pendingInputs = AtomicInteger(0)
    private lateinit var captureThread: HandlerThread
    private lateinit var capture: Handler
    private var projection: MediaProjection? = null
    private var virtualDisplay: VirtualDisplay? = null
    private var imageReader: ImageReader? = null
    @Volatile private var socket: Socket? = null
    @Volatile private var selectedDevice: Device? = null
    @Volatile private var expectedCertificate: ByteArray? = null
    @Volatile private var lastPeerActivity = 0L
    @Volatile private var sourceWidth = 0
    @Volatile private var sourceHeight = 0
    private var controlAllowed = false
    private var started = false
    private var lastEncoded = 0L
    private var inputWindow = 0L
    private var inputCount = 0

    private val screenOff = object : BroadcastReceiver() {
        override fun onReceive(context: Context?, intent: Intent?) {
            stopWithReason("Показ завершён: телефон заблокирован")
        }
    }

    private val watchdog = object : Runnable {
        override fun run() {
            if (stopped.get()) return
            val device = selectedDevice
            val expected = expectedCertificate
            val locked = getSystemService(KeyguardManager::class.java).isKeyguardLocked
            if (locked || device == null || !device.isPaired || !device.isReachable ||
                (expected != null && !MessageDigest.isEqual(device.certificate.encoded, expected))) {
                stopWithReason("Показ остановлен: телефон заблокирован или связь отключена")
                return
            }
            if (lastPeerActivity > 0 && SystemClock.elapsedRealtime() - lastPeerActivity > 14000) {
                stopWithReason("Связь с компьютером потеряна. Нажми «Показать экран» ещё раз.")
                return
            }
            main.postDelayed(this, 1000)
        }
    }

    override fun onCreate() {
        super.onCreate()
        captureThread = HandlerThread("VELIQEN screen capture").apply { start() }
        capture = Handler(captureThread.looper)
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(screenOff, IntentFilter(Intent.ACTION_SCREEN_OFF), RECEIVER_NOT_EXPORTED)
        else @Suppress("DEPRECATION") registerReceiver(screenOff, IntentFilter(Intent.ACTION_SCREEN_OFF))
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) { stopWithReason(null); return START_NOT_STICKY }
        if (started || intent == null) return START_NOT_STICKY
        started = true
        val id = intent.getStringExtra("deviceId")
        val device = KdeConnect.getInstance().getDevice(id)
        @Suppress("DEPRECATION")
        val projectionData = intent.getParcelableExtra<Intent>("projectionData")
        val resultCode = intent.getIntExtra("resultCode", Activity.RESULT_CANCELED)
        if (device?.isPaired != true || device.trustedLanAddress == null || projectionData == null || resultCode != Activity.RESULT_OK) {
            stopWithReason("Компьютер недоступен. Открой VELIQEN Link на Ubuntu и попробуй ещё раз.")
            return START_NOT_STICKY
        }
        selectedDevice = device
        expectedCertificate = device.certificate.encoded
        controlAllowed = Build.VERSION.SDK_INT >= 24 && intent.getBooleanExtra("control", false) && VeliqenScreenInputService.instance != null
        lastError = null
        val notification = notification(device.name)
        try {
            if (Build.VERSION.SDK_INT >= 29) startForeground(NOTIFICATION_ID, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION)
            else startForeground(NOTIFICATION_ID, notification)
        } catch (_: Exception) {
            stopWithReason("Android не разрешил показ. Открой приложение на телефоне и начни показ заново.")
            return START_NOT_STICKY
        }
        main.post(watchdog)
        thread(name = "VELIQEN screen connection", isDaemon = true) {
            connectAndSend(device, projectionData, resultCode)
        }
        return START_NOT_STICKY
    }

    private fun notification(computer: String): android.app.Notification {
        val manager = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26) manager.createNotificationChannel(NotificationChannel(CHANNEL, "Показ экрана", NotificationManager.IMPORTANCE_LOW))
        val flags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        val stop = PendingIntent.getService(this, 5801, Intent(this, ScreenShareService::class.java).setAction(ACTION_STOP), flags)
        val open = PendingIntent.getActivity(this, 5802, Intent(this, ScreenShareActivity::class.java)
            .putExtra("deviceId", selectedDevice?.deviceId), flags)
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Экран на $computer")
            .setContentText(if (controlAllowed) "Показ и управление включены. Нажми «Остановить», чтобы завершить." else "Показ включён. Управление с компьютера отключено.")
            .setOngoing(true).setContentIntent(open)
            .addAction(0, "Остановить", stop).build()
    }

    private fun connectAndSend(device: Device, data: Intent, resultCode: Int) {
        try {
            val address = device.trustedLanAddress ?: throw IOException("No paired LAN link")
            val raw = Socket()
            socket = raw
            raw.connect(InetSocketAddress(address, PORT), 8000)
            if (stopped.get()) return
            val secure = SslHelper.convertToSslSocket(this, raw, device.deviceId, true, true)
            socket = secure
            secure.startHandshake()
            val actual = secure.session.peerCertificates.firstOrNull()?.encoded ?: throw IOException("Missing peer certificate")
            if (!device.isPaired || !MessageDigest.isEqual(actual, device.certificate.encoded)) throw IOException("Different paired computer")
            val input = DataInputStream(BufferedInputStream(secure.inputStream))
            val output = DataOutputStream(BufferedOutputStream(secure.outputStream))
            writeJson(output, JSONObject().put("type", "hello").put("protocol", 1)
                .put("deviceId", DeviceHelper.getDeviceId(this)).put("name", DeviceHelper.getDeviceName(this))
                .put("control", controlAllowed))
            val ready = readJson(input)
            if (ready.optString("op") != "ready" || ready.optInt("protocol") != 1) throw IOException("Receiver not ready")
            secure.soTimeout = 0 // Closed by the independent consent/lock/link watchdog.
            lastPeerActivity = SystemClock.elapsedRealtime()
            main.post {
                if (!stopped.get()) {
                    try {
                        val manager = getSystemService(MediaProjectionManager::class.java)
                        projection = manager.getMediaProjection(resultCode, data)
                        projection!!.registerCallback(object : MediaProjection.Callback() {
                            override fun onStop() { stopWithReason(null) }
                            override fun onCapturedContentResize(width: Int, height: Int) {
                                if (width > 0 && height > 0 && !stopped.get()) resizeSurface(width, height)
                            }
                        }, capture)
                        val bounds = screenSize()
                        capture.post { if (!stopped.get()) resizeSurface(bounds.first, bounds.second) }
                        activeDeviceId = device.deviceId
                    } catch (_: Exception) {
                        stopWithReason("Разрешение показа завершилось. Нажми «Показать экран» ещё раз.")
                    }
                }
            }
            thread(name = "VELIQEN screen commands", isDaemon = true) {
                try {
                    while (!stopped.get()) {
                        val command = readJson(input)
                        lastPeerActivity = SystemClock.elapsedRealtime()
                        if (command.optString("op") == "stop") { stopWithReason(null); break }
                        if (command.optString("op") in setOf("tap", "swipe", "back", "home", "recents", "text") && pendingInputs.get() < 4) {
                            pendingInputs.incrementAndGet()
                            main.post { try { handleInput(command) } finally { pendingInputs.decrementAndGet() } }
                        }
                    }
                } catch (_: Exception) { stopWithReason(null) }
            }
            while (!stopped.get()) {
                val frame = frames.poll(2, TimeUnit.SECONDS)
                if (frame != null) {
                    output.writeInt(frame.size + 1); output.writeByte(1); output.write(frame); output.flush()
                } else writeJson(output, JSONObject().put("type", "heartbeat"))
            }
        } catch (_: Exception) {
            if (!stopped.get()) stopWithReason("Не удалось открыть экран. Открой VELIQEN Link на Ubuntu, выбери этот телефон и проверь Wi-Fi.")
        } finally {
            try { socket?.close() } catch (_: IOException) { }
        }
    }

    @Suppress("DEPRECATION")
    private fun screenSize(): Pair<Int, Int> {
        val window = getSystemService(WindowManager::class.java)
        return if (Build.VERSION.SDK_INT >= 30) window.maximumWindowMetrics.bounds.let { it.width() to it.height() }
        else DisplayMetrics().also { window.defaultDisplay.getRealMetrics(it) }.let { it.widthPixels to it.heightPixels }
    }

    /** Runs on the capture handler. A projection token is never reused. */
    private fun resizeSurface(width: Int, height: Int) {
        if (stopped.get() || width <= 0 || height <= 0) return
        if (imageReader != null && sourceWidth == width && sourceHeight == height) return
        sourceWidth = width; sourceHeight = height
        val scale = min(1.0, min(900.0 / width, 1280.0 / height))
        val w = (width * scale).roundToInt().coerceAtLeast(2)
        val h = (height * scale).roundToInt().coerceAtLeast(2)
        try {
            val old = imageReader
            val reader = ImageReader.newInstance(w, h, PixelFormat.RGBA_8888, 3)
            reader.setOnImageAvailableListener({ available -> captureFrame(available) }, capture)
            imageReader = reader
            if (virtualDisplay == null) {
                virtualDisplay = projection?.createVirtualDisplay("VELIQEN screen", w, h,
                    resources.configuration.densityDpi, DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                    reader.surface, null, capture)
            } else {
                virtualDisplay?.surface = null
                virtualDisplay?.resize(w, h, resources.configuration.densityDpi)
                virtualDisplay?.surface = reader.surface
            }
            old?.setOnImageAvailableListener(null, null)
            old?.close()
        } catch (_: Exception) {
            stopWithReason("Android завершил показ экрана. Начни новый сеанс из приложения.")
        }
    }

    private fun captureFrame(reader: ImageReader) {
        val image = try { reader.acquireLatestImage() } catch (_: Exception) { null } ?: return
        try {
            val now = SystemClock.elapsedRealtime()
            if (stopped.get() || now - lastEncoded < 125) return
            if (getSystemService(KeyguardManager::class.java).isKeyguardLocked) { stopWithReason(null); return }
            lastEncoded = now
            val plane = image.planes[0]
            val paddedWidth = image.width + (plane.rowStride - plane.pixelStride * image.width) / plane.pixelStride
            val bitmap = Bitmap.createBitmap(paddedWidth, image.height, Bitmap.Config.ARGB_8888)
            try {
                plane.buffer.rewind()
                bitmap.copyPixelsFromBuffer(plane.buffer)
                val cropped = if (paddedWidth == image.width) bitmap else Bitmap.createBitmap(bitmap, 0, 0, image.width, image.height)
                try {
                    val encoded = ByteArrayOutputStream(96 * 1024)
                    cropped.compress(Bitmap.CompressFormat.JPEG, 65, encoded)
                    val bytes = encoded.toByteArray()
                    if (bytes.size <= MAX_FRAME) { frames.poll(); frames.offer(bytes) }
                } finally { if (cropped !== bitmap) cropped.recycle() }
            } finally { bitmap.recycle() }
        } catch (_: Exception) {
            stopWithReason("Не удалось получить кадр экрана. Начни показ заново.")
        } finally { image.close() }
    }

    private fun handleInput(command: JSONObject) {
        if (stopped.get() || !controlAllowed || activeDeviceId == null) return
        val device = selectedDevice ?: return
        if (!device.isPaired || getSystemService(KeyguardManager::class.java).isKeyguardLocked) return
        val expected = expectedCertificate ?: return
        if (!MessageDigest.isEqual(device.certificate.encoded, expected)) { stopWithReason(null); return }
        val service = VeliqenScreenInputService.instance ?: return
        val size = screenSize()
        // If Android/OEM shares a cropped application window, allow viewing only.
        if (size.first != sourceWidth || size.second != sourceHeight) return
        val now = SystemClock.elapsedRealtime()
        if (now - inputWindow >= 1000) { inputWindow = now; inputCount = 0 }
        if (++inputCount > 30) return
        when (command.optString("op")) {
            "back" -> service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_BACK)
            "home" -> service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_HOME)
            "recents" -> service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_RECENTS)
            "text" -> command.optString("text").takeIf { it.isNotEmpty() && it.length <= 1024 }?.let { service.typeText(it) }
            "tap", "swipe" -> {
                fun coordinate(key: String, maximum: Int): Float? {
                    val value = command.optDouble(key, Double.NaN)
                    return if (value.isFinite() && value in 0.0..1.0) (value * (maximum - 1)).toFloat() else null
                }
                val x = coordinate("x", size.first) ?: return
                val y = coordinate("y", size.second) ?: return
                if (command.optString("op") == "tap") service.gesture(x, y, null, null, 40)
                else service.gesture(x, y, coordinate("x2", size.first) ?: return,
                    coordinate("y2", size.second) ?: return, command.optLong("duration", 300).coerceIn(80, 1000))
            }
        }
    }

    private fun writeJson(output: DataOutputStream, value: JSONObject) {
        val bytes = value.toString().toByteArray(Charsets.UTF_8)
        require(bytes.size <= MAX_JSON)
        output.writeInt(bytes.size + 1); output.writeByte(0); output.write(bytes); output.flush()
    }

    private fun readJson(input: DataInputStream): JSONObject {
        val length = input.readInt()
        if (length < 3 || length > MAX_JSON + 1 || input.readUnsignedByte() != 0) throw IOException("Invalid screen command")
        val bytes = ByteArray(length - 1)
        input.readFully(bytes)
        return JSONObject(bytes.toString(Charsets.UTF_8))
    }

    private fun stopWithReason(reason: String?) {
        if (!stopped.compareAndSet(false, true)) return
        if (reason != null) lastError = reason
        try { socket?.close() } catch (_: IOException) { }
        main.post {
            if (reason != null) Toast.makeText(this, reason, Toast.LENGTH_LONG).show()
            stopSelf()
        }
    }

    override fun onDestroy() {
        stopped.set(true)
        activeDeviceId = null
        main.removeCallbacks(watchdog)
        try { unregisterReceiver(screenOff) } catch (_: Exception) { }
        try { socket?.close() } catch (_: IOException) { }
        capture.post {
            imageReader?.setOnImageAvailableListener(null, null)
            virtualDisplay?.release(); virtualDisplay = null
            imageReader?.close(); imageReader = null
            projection?.stop(); projection = null
            frames.clear()
            captureThread.quitSafely()
        }
        if (Build.VERSION.SDK_INT >= 24) stopForeground(STOP_FOREGROUND_REMOVE)
        else @Suppress("DEPRECATION") stopForeground(true)
        super.onDestroy()
    }

    companion object {
        const val PORT = 38475
        const val REQUEST_MESSAGE = "VELIQEN_LINK_SCREEN_REQUEST_V1"
        const val ACTION_STOP = "org.kde.kdeconnect.veliqen.STOP_SCREEN"
        private const val CHANNEL = "veliqen_screen"
        private const val NOTIFICATION_ID = 5800
        private const val MAX_JSON = 16 * 1024
        private const val MAX_FRAME = 2 * 1024 * 1024
        @Volatile var activeDeviceId: String? = null
            private set
        @Volatile var lastError: String? = null
            private set
        fun stop(context: Context) { context.stopService(Intent(context, ScreenShareService::class.java)) }
    }
}
