# VELIQEN Link 1.2.1: developer documentation

This document covers work with the source code. Installation of the ready-to-use DEB and APK is described in the [main README](../../README.md).

[Подробная русская инструкция](BUILDING-RU.md)

## Source tree

The Ubuntu application is in `linux/`, the complete Android project is in `android/kdeconnect-android/`, ROM integration is in `rom/`, and bundled scrcpy components and corresponding source are in `vendor/`. The full release kit places finished installation files in `Установочные-файлы/`. Android build output and verification reports use `android/build-output/`.

Validation records are in [VALIDATION.md](../VALIDATION.md) and the root `RELEASE-CHECKS.json`. Tests are in `tests/`, `linux/test_kde.py` and `rom/test_integrator.py`.

## Ubuntu package

From the project root:

```bash
python3 BUILD-PACKAGES.py
```

The package is written to `Установочные-файлы/`. Its system dependencies are declared by `BUILD-PACKAGES.py` and resolved by APT during installation.

## Android environment

Use JDK 21, Android SDK `platforms;android-37.0`, Build Tools `37.0.0`, platform-tools and the included Gradle 9.6.0 wrapper. Dependency downloads need access to Google Maven, Maven Central, JitPack and Gradle.

Open `android/kdeconnect-android/` in Android Studio and select the `veliqen` variant. The KDE source namespace remains `org.kde.kdeconnect_tp`; the public application ID is `tech.veliqen.link`, with versionCode `13706`.

## Signed public APK

Restore the existing release key and its password file from the private backup to `${XDG_DATA_HOME:-$HOME/.local/share}/veliqen-link/signing/`. Keep the public application ID and signing key unchanged for updates. The certificate is recorded in [RELEASE-SIGNING.json](../../android/RELEASE-SIGNING.json).

From the project root:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
bash android/BUILD-APK.sh --release
python3 android/VERIFY-APK.py android/build-output/VELIQEN-Link-1.2.1-release.apk
```

The release command does not generate a new key when the existing one is missing. `android/INIT-SIGNING.sh` is an explicit setup command for a separate new project; a newly generated key cannot update the already published application.

CI signing accepts `VELIQEN_KEYSTORE`, `VELIQEN_STORE_PASSWORD`, `VELIQEN_KEY_ALIAS` and `VELIQEN_KEY_PASSWORD`. Store credentials in Secrets, not the repository. `VELIQEN_SIGNING_DIR` selects a different key directory. Passwords are not printed or passed as command arguments. The script does not change the system JDK or SDK.

`--offline` uses an already populated Gradle cache. `--personal` is an alias for `--release`.

## Other Android variants and ROM integration

```bash
bash android/BUILD-APK.sh --unsigned
```

Unsigned APKs require signing before ordinary Android installation. `--development` creates the separate `tech.veliqen.link.development` application using the public upstream debug key; it is not the public release.

The full and ROM kits contain an unsigned prebuilt at `rom/payload/prebuilt/VELIQENLink.apk`. The source archive omits APKs; after `--unsigned`, the integrator can use the output in `android/build-output/`. Follow [rom/README-RU.md](../../rom/README-RU.md) and first run the integrator with `--dry-run` using the actual AOSP/LineageOS tree and product makefile paths.

The platform build signs the system app with the ROM's `platform` key. Background clipboard access requires the appropriate system signature or an Android-supported role; copying an APK to `priv-app` alone is insufficient. The public APK cannot update a system app with a different signature. Retain the ROM's signing identity for ROM updates. Integration does not flash devices or remove legacy applications automatically.

## Publication and licensing

Use [the release guide](../GITHUB-RELEASE.md) for publication. Keep the corresponding source, third-party source bundle, licenses, copyright notices and checksums available with the binaries.

The GitHub Actions workflow builds the DEB and an unsigned Android file without private signing credentials. It does not publish releases. Local validation is not proof that the workflow succeeded in a destination repository.

See [LICENSE](../../LICENSE), [THIRD_PARTY.md](../../THIRD_PARTY.md), [android/UPSTREAM.json](../../android/UPSTREAM.json), [android/CHANGES-VELIQEN.md](../../android/CHANGES-VELIQEN.md), the upstream `COPYING` and `LICENSES/` files, and `vendor/SOURCES.md`.

Private release and platform signing keys are excluded from public source and release archives. The public upstream `debug.keystore` is included only for the separate development variant.
