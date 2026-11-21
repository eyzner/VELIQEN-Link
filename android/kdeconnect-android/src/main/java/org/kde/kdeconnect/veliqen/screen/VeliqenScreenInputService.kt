// SPDX-License-Identifier: GPL-3.0-or-later
package org.kde.kdeconnect.veliqen.screen

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.os.Bundle
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo

/** Input is called only by the active, explicitly authorised screen session. */
class VeliqenScreenInputService : AccessibilityService() {
    override fun onServiceConnected() {
        instance = this
    }

    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit
    override fun onInterrupt() = Unit

    override fun onDestroy() {
        if (instance === this) instance = null
        super.onDestroy()
    }

    fun gesture(x: Float, y: Float, x2: Float?, y2: Float?, duration: Long) {
        val path = Path().apply {
            moveTo(x, y)
            if (x2 != null && y2 != null) lineTo(x2, y2)
        }
        dispatchGesture(GestureDescription.Builder()
            .addStroke(GestureDescription.StrokeDescription(path, 0, duration.coerceIn(1, 1000)))
            .build(), null, null)
    }

    fun typeText(text: String) {
        val node = rootInActiveWindow?.findFocus(AccessibilityNodeInfo.FOCUS_INPUT) ?: return
        // Do not inspect or rewrite protected password fields.
        if (!node.isEditable || node.isPassword || text.length > 1024) return
        val arguments = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, arguments)
    }

    companion object {
        @Volatile var instance: VeliqenScreenInputService? = null
            private set
    }
}
