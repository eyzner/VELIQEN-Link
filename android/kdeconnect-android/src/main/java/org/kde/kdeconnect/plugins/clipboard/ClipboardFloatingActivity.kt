/*
 * SPDX-FileCopyrightText: 2020 Anjani Kumar <anjanik012@gmail.com>
 * SPDX-FileCopyrightText: 2021 Ilmaz Gumerov <ilmaz1309@gmail.com>
 *
 * SPDX-License-Identifier: GPL-2.0-only OR GPL-3.0-only OR LicenseRef-KDE-Accepted-GPL
 */

package org.kde.kdeconnect.plugins.clipboard

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.view.WindowManager
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.kde.kdeconnect_tp.R

/*
    User-initiated clipboard activity for the notification action / quick settings tile.
    VELIQEN does not start this activity from logcat. Automatic background sync uses
    READ_CLIPBOARD_IN_BACKGROUND in the platform-signed ROM build. The regular APK
    reads the clipboard only when the user explicitly opens this foreground action.
 */
class ClipboardFloatingActivity : AppCompatActivity() {

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) {
            // We are now sure that clipboard can be accessed from here.
            ClipboardListener.instance(this).onClipboardChanged()
            if (shouldShowToast()) {
                Toast.makeText(this, R.string.pref_plugin_clipboard_sent, Toast.LENGTH_SHORT).show()
            }
            finish()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_clipboard_floating)
        val wlp = window.attributes
        wlp.dimAmount = 0f
        wlp.flags = WindowManager.LayoutParams.FLAG_LAYOUT_NO_LIMITS or
                WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL

        window.attributes = wlp
    }

    private fun shouldShowToast(): Boolean {
        return intent.getBooleanExtra(KEY_SHOW_TOAST, false)
    }

    companion object {
        private const val KEY_SHOW_TOAST = "SHOW_TOAST"

        fun getIntent(context: Context, showToast: Boolean): Intent {
            return Intent(context.applicationContext, ClipboardFloatingActivity::class.java).apply {
                putExtra(KEY_SHOW_TOAST, showToast)
                flags = Intent.FLAG_ACTIVITY_CLEAR_TASK or Intent.FLAG_ACTIVITY_NEW_TASK
            }
        }
    }
}
