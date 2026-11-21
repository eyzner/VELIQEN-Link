// SPDX-License-Identifier: GPL-3.0-or-later
package org.kde.kdeconnect.veliqen.screen

import android.app.Activity
import android.app.AlertDialog
import android.content.ComponentName
import android.content.Intent
import android.content.res.ColorStateList
import android.graphics.Color
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.RippleDrawable
import android.media.projection.MediaProjectionConfig
import android.media.projection.MediaProjectionManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.widget.Button
import android.widget.CheckBox
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import org.kde.kdeconnect.KdeConnect

/** Visible consent entry point. It never silently resumes an old projection token. */
class ScreenShareActivity : AppCompatActivity() {
    private lateinit var control: CheckBox
    private lateinit var hint: TextView
    private var pendingControl = false
    private val deviceId: String? get() = intent.getStringExtra("deviceId")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pendingControl = savedInstanceState?.getBoolean("pendingControl")
            ?: getSharedPreferences("veliqen_screen", MODE_PRIVATE).getBoolean("control_${deviceId}", false)
        title = "Экран на компьютере"
        val dark = resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK == android.content.res.Configuration.UI_MODE_NIGHT_YES
        val ink = if (dark) Color.rgb(242, 242, 244) else Color.rgb(24, 25, 28)
        val muted = if (dark) Color.rgb(180, 182, 191) else Color.rgb(92, 96, 108)
        val accent = if (dark) Color.rgb(255, 137, 148) else Color.rgb(175, 40, 58)
        val onAccent = if (dark) Color.rgb(77, 23, 32) else Color.WHITE
        val layout = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(24), dp(24), dp(24))
        }
        val scroll = ScrollView(this).apply {
            setBackgroundColor(if (dark) Color.rgb(16, 17, 19) else Color.rgb(246, 245, 244))
            addView(layout)
        }
        setContentView(scroll)
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { view, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        fun text(value: String, size: Float, color: Int) = TextView(this).apply {
            text = value; textSize = size; setTextColor(color)
            setPadding(0, dp(8), 0, dp(12))
            layout.addView(this)
        }
        val device = KdeConnect.getInstance().getDevice(deviceId)
        text("Показать экран", 28f, ink)
        text("На компьютере: ${device?.name ?: "не выбран"}", 18f, ink)
        text("Экран откроется в VELIQEN Link на Ubuntu. Компьютер уже сопряжён — адреса, коды и отладка USB не нужны.", 16f, muted)
        control = CheckBox(this).apply {
            text = "Разрешить управление в этом сеансе"
            textSize = 16f
            minHeight = dp(56)
            setTextColor(ink)
            isChecked = pendingControl
            isEnabled = Build.VERSION.SDK_INT >= 24
            buttonTintList = ColorStateList.valueOf(accent)
        }
        layout.addView(control)
        hint = text("Для просмотра достаточно следующего системного разрешения. Управление — отдельно, по твоему выбору.", 14f, muted)
        val share = Button(this).apply {
            text = "Показать экран"
            isAllCaps = false
            minHeight = dp(56)
            setTextColor(onAccent)
            background = RippleDrawable(ColorStateList.valueOf(0x22ffffff), GradientDrawable().apply {
                setColor(accent); cornerRadius = dp(18).toFloat()
            }, null)
            setOnClickListener { begin() }
        }
        layout.addView(share, LinearLayout.LayoutParams(-1, -2).apply { topMargin = dp(18) })
        val cancel = Button(this).apply {
            text = "Назад"; isAllCaps = false; minHeight = dp(52)
            setTextColor(ink)
            backgroundTintList = ColorStateList.valueOf(if (dark) Color.rgb(48, 48, 55) else Color.rgb(236, 233, 239))
            setOnClickListener { finish() }
        }
        layout.addView(cancel, LinearLayout.LayoutParams(-1, -2))
        if (ScreenShareService.activeDeviceId != null) {
            share.text = "Остановить показ"
            share.setOnClickListener { ScreenShareService.stop(this); finish() }
            control.isEnabled = false
            hint.text = "Экран уже передаётся. Чтобы изменить управление или выбрать другой компьютер, заверши текущий показ."
        } else if (device?.isPaired != true || device.trustedLanAddress == null) {
            share.isEnabled = false
            hint.text = "Сначала подключи телефон и компьютер к одной Wi-Fi-сети и подтверди сопряжение в VELIQEN Link."
        }
    }

    private fun dp(value: Int): Int = (value * resources.displayMetrics.density).toInt()

    private fun begin() {
        val device = KdeConnect.getInstance().getDevice(deviceId)
        if (device?.isPaired != true || device.trustedLanAddress == null) {
            hint.text = "Компьютер сейчас недоступен. Открой VELIQEN Link на Ubuntu и проверь Wi-Fi."
            return
        }
        if (control.isChecked && VeliqenScreenInputService.instance == null) {
            AlertDialog.Builder(this)
                .setTitle("Разрешить управление")
                .setMessage("В специальных возможностях включи «VELIQEN: управление экраном», затем вернись сюда. Если Android блокирует переключатель: Настройки → Приложения → VELIQEN Link → меню → Разрешить ограниченные настройки (если этот пункт есть). Нажатия принимаются только во время показа. Можно продолжить только просмотр.")
                .setPositiveButton("Открыть настройки") { _, _ ->
                    val component = ComponentName(this, VeliqenScreenInputService::class.java)
                    val details = Intent("android.settings.ACCESSIBILITY_DETAILS_SETTINGS")
                        .putExtra(Intent.EXTRA_COMPONENT_NAME, component.flattenToString())
                    try { startActivity(details) }
                    catch (_: Exception) { startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)) }
                }
                .setNeutralButton("Только просмотр") { _, _ -> control.isChecked = false; requestProjection() }
                .setNegativeButton("Отмена", null).show()
            return
        }
        requestProjection()
    }

    private fun requestProjection() {
        pendingControl = Build.VERSION.SDK_INT >= 24 && control.isChecked && VeliqenScreenInputService.instance != null
        getSharedPreferences("veliqen_screen", MODE_PRIVATE).edit()
            .putBoolean("control_${deviceId}", control.isChecked).apply()
        val manager = getSystemService(MediaProjectionManager::class.java)
        val capture = if (Build.VERSION.SDK_INT >= 34) manager.createScreenCaptureIntent(MediaProjectionConfig.createConfigForDefaultDisplay())
            else manager.createScreenCaptureIntent()
        @Suppress("DEPRECATION")
        startActivityForResult(capture, CAPTURE)
    }

    @Deprecated("Framework activity result contract")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != CAPTURE) return
        if (resultCode != Activity.RESULT_OK || data == null) {
            hint.text = "Показ не начат. Нажми «Показать экран», когда будешь готов."
            return
        }
        val start = Intent(this, ScreenShareService::class.java)
            .putExtra("deviceId", deviceId)
            .putExtra("resultCode", resultCode)
            .putExtra("projectionData", data)
            .putExtra("control", pendingControl)
        try {
            ContextCompat.startForegroundService(this, start)
            finish()
        } catch (_: Exception) {
            hint.text = "Android не разрешил начать показ. Вернись в приложение и попробуй ещё раз."
        }
    }

    override fun onSaveInstanceState(outState: Bundle) {
        outState.putBoolean("pendingControl", if (::control.isInitialized) control.isChecked else pendingControl)
        super.onSaveInstanceState(outState)
    }

    companion object { private const val CAPTURE = 5701 }
}
