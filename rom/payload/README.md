# VELIQEN Link system package

This directory is installed by the reversible VELIQEN Link ROM integration tool.
Android client: independent KDE Connect adaptation; package `tech.veliqen.link`.
Upstream commit: `6b9e7b2cf371c5b40cf75079cc21e4e66b4b464d`.
Upstream and modifications retain their respective GPL-compatible license notices.

`android_app_import` re-signs the APK with this ROM's **own platform certificate**.
The public release uses its own private signer. Its APK cannot replace a platform-signed system app with the same ID.
The installer does not install, replace, export or create a ROM platform private key.

Android 17 `READ_CLIPBOARD_IN_BACKGROUND` is signature|role. Merely placing an APK
under `priv-app` does not grant it. Runtime data permissions remain user choices.
No Settings source patch, vendor HAL patch, SELinux bypass or device-file rewrite
is performed. This integration does not make all GSI device hardware compatible.
