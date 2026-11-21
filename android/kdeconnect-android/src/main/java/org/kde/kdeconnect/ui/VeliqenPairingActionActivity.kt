/* SPDX-License-Identifier: GPL-3.0-or-later */
package org.kde.kdeconnect.ui

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import org.kde.kdeconnect.KdeConnect
import org.kde.kdeconnect.PairingHandler

/** Non-exported entry point for immutable, one-shot pairing notification actions. */
class VeliqenPairingActionActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val id = intent.getStringExtra(MainActivity.EXTRA_DEVICE_ID)
        val device = KdeConnect.getInstance().getDevice(id)
        if (device?.pairStatus == PairingHandler.PairState.RequestedByPeer) {
            when (intent.getStringExtra(MainActivity.PAIR_REQUEST_STATUS)) {
                MainActivity.PAIRING_ACCEPTED -> device.acceptPairing()
                MainActivity.PAIRING_REJECTED -> device.cancelPairing()
            }
        }
        startActivity(Intent(this, VeliqenHomeActivity::class.java)
            .putExtra(MainActivity.EXTRA_DEVICE_ID, id)
            .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP))
        finish()
    }
}
