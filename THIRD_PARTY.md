# Open components and source provenance

## VELIQEN desktop integration and GNOME extension

The original Python desktop integration, native GTK interface, packaging and
GNOME clipboard bridge in this kit are provided under GPL-3.0-or-later.
See LICENSE. They are independent desktop clients of system D-Bus APIs and
external programs. They do not replace the authorship of those components.

## KDE Connect for Android

The complete adapted source is in android/kdeconnect-android. Original copyright
headers, COPYING, license resources and dependency notices are retained. The
precise upstream commit and local changes are recorded in android/UPSTREAM.json
and android/VELIQEN-ANDROID.patch.

Upstream: https://invent.kde.org/network/kdeconnect-android
Mirror: https://github.com/KDE/kdeconnect-android

The public Android application uses the independent application ID
`tech.veliqen.link` and a permanent VELIQEN release signing certificate.
The KDE source namespace and network protocol remain compatible. The original
public upstream debug keystore is retained solely for development builds with
the separate `tech.veliqen.link.development` ID. It is not the release signing
key. Private release/platform keys are never included in public artifacts.
ROM integration is signed by the ROM's platform key during the ROM build.

## KDE Connect desktop

Installed from the user's Ubuntu/Debian repositories. Not bundled as a modified
desktop daemon. The integration calls its D-Bus APIs and delegates transfer
notifications and some dialogs to the existing software.

Upstream: https://invent.kde.org/network/kdeconnect-kde
Mirror: https://github.com/KDE/kdeconnect-kde
API revision inspected: 6fa6dac15f1019bff6a922cf3014e222f5140f66.
See linux/KDE_BACKEND.md.

## scrcpy

The vendor archive is the unmodified official Linux x86_64 distribution of
scrcpy 5.0.1, including bundled ADB. Exact URL and SHA-256 are in
vendor/SCRCPY_PROVENANCE.json. The DEB builder verifies that SHA-256 and installs
the selected original files unchanged. Their authorship and licenses are
preserved.

Source: https://github.com/Genymobile/scrcpy
Release: https://github.com/Genymobile/scrcpy/releases/tag/v5.0.1
Main license: Apache-2.0. Bundled libraries retain their upstream notices.
Corresponding source archives, dependency versions, checksums, build/relink
instructions and attribution are provided in `vendor/SOURCES.md`,
`vendor/sources/` and `vendor/licenses/`. These include scrcpy, FFmpeg, SDL,
dav1d and libusb, plus the relevant Ubuntu build-dependency sources and patches.
The Android platform-tools notices are retained separately. Do not describe
the entire bundled distribution as exclusively Apache-2.0: its components
include LGPL and other licenses. Publish the Source archive together with the
binary DEB.

Users may inspect, modify and rebuild those open-source components and replace
the private tool bundle. No restriction on reverse engineering necessary to
debug modifications to the LGPL libraries is added by VELIQEN Link.

## PipeWire, WirePlumber, BlueZ and GTK

These are system dependencies installed from the operating-system repository.
Their source, authors and licenses remain those of the respective projects:

- https://pipewire.org/
- https://gitlab.freedesktop.org/pipewire/pipewire
- https://pipewire.pages.freedesktop.org/wireplumber/
- https://www.bluez.org/
- https://www.gtk.org/
- https://pygobject.gnome.org/

Telephony API reference:
https://github.com/PipeWire/pipewire/blob/master/spa/plugins/bluez5/README-Telephony.md

GNOME clipboard integration was implemented against the official St/Meta APIs,
with the owner-change ordering behavior checked against GSConnect's source:
https://github.com/GSConnect/gnome-shell-extension-gsconnect/blob/main/src/shell/clipboard.js
No GSConnect extension or daemon is installed by this kit.

## Android platform documentation

- https://developer.android.com/about/versions/10/privacy/changes
- https://android.googlesource.com/platform/frameworks/base/+/android17-release/core/res/AndroidManifest.xml
- https://developer.android.com/reference/android/provider/Telephony
- https://developer.android.com/tools/adb
- https://developer.android.com/security/fraud-prevention/activities

These references describe platform behavior; the device-specific ROM and
hardware validation requirements are recorded separately in docs/VALIDATION.md.
