# Техническая документация VELIQEN Link 1.2.1

Этот файл относится к работе с исходным кодом. Пользовательская установка готовых DEB и APK описана в [инструкции](../INSTALLATION.md).

[English](BUILDING.md)

## Состав исходников

- `linux/` — Ubuntu-приложение.
- `android/kdeconnect-android/` — полный Android-проект на основе KDE Connect 1.37.3.
- `rom/` — интегратор AOSP/LineageOS и инструкция отката.
- `vendor/` — официальный scrcpy x86_64 и соответствующие исходники поставляемых компонентов.
- `tests/`, `linux/test_kde.py`, `rom/test_integrator.py` — проверки.
- `Установочные-файлы/` — готовые DEB, APK и расширение GNOME в полном комплекте.
- `android/build-output/` — результаты Android-сборки и отчёты проверок.

Результаты выполненных проверок находятся в [VALIDATION.md](../VALIDATION.md) и `RELEASE-CHECKS.json` в корне проекта. Снимки интерфейса находятся в `docs/screenshots/`.

## Ubuntu-пакет

Из корня проекта:

```bash
python3 BUILD-PACKAGES.py
```

Готовый пакет появится в `Установочные-файлы/`. Для его установки APT разрешает зависимости, объявленные в DEB; сами пакеты Ubuntu в DEB не вкладываются. Точные зависимости и состав задаются в `BUILD-PACKAGES.py`.

## Android: окружение

Нужны JDK 21, Android SDK `platforms;android-37.0`, `build-tools;37.0.0`, platform-tools и доступ к Google Maven, Maven Central, JitPack и Gradle. Gradle Wrapper 9.6.0 входит в исходники.

В Android Studio открой `android/kdeconnect-android/`, выбери JDK 21 и вариант `veliqen`. Namespace исходников KDE остаётся `org.kde.kdeconnect_tp`; он не определяет ID установленного приложения.

Публичное приложение имеет ID `tech.veliqen.link`, versionCode `13706`. Сертификат и его SHA-256 описаны в [RELEASE-SIGNING.json](../../android/RELEASE-SIGNING.json). ID и ключ подписи опубликованного приложения необходимо сохранять для последующих обновлений.

## Android: выпуск с постоянной подписью

Для обновления существующего приложения используй уже созданный ключ выпуска. Файлы `veliqen-link-release.p12` и `store-password` из личной резервной копии должны находиться в `${XDG_DATA_HOME:-$HOME/.local/share}/veliqen-link/signing/`.

Из корня проекта:

```bash
export ANDROID_HOME="$HOME/Android/Sdk"
bash android/BUILD-APK.sh --release
python3 android/VERIFY-APK.py android/build-output/VELIQEN-Link-1.2.1-release.apk
```

`--release` не создаёт новый ключ при его отсутствии. Для отдельного нового проекта существует явная команда `bash android/INIT-SIGNING.sh`; она отказывается заменять существующие ключ или пароль. Новый ключ не подходит для обновления опубликованного VELIQEN Link.

Для CI предусмотрены `VELIQEN_KEYSTORE`, `VELIQEN_STORE_PASSWORD`, `VELIQEN_KEY_ALIAS` и `VELIQEN_KEY_PASSWORD`. Значения должны храниться в Secrets, а не в репозитории. Альтернативный каталог ключа задаётся через `VELIQEN_SIGNING_DIR`. Пароли не передаются в аргументах команд и не печатаются. Скрипт не меняет системные Java и SDK.

Для работы с заполненным кэшем Gradle добавь `--offline`. Параметр `--personal` сохранён как синоним `--release`.

## Android: другие варианты

```bash
bash android/BUILD-APK.sh --unsigned
```

`--unsigned` создаёт APK для последующей подписи или интеграции в прошивку. Обычный установщик Android не устанавливает неподписанный APK.

`--development` создаёт отдельное тестовое приложение `tech.veliqen.link.development` с общедоступным debug-ключом upstream. Его нельзя выдавать за публичный выпуск. Стандартный вариант Android-проекта остаётся неподписанным, если переменные `VELIQEN_*` для подписи не заданы явно.

## Интеграция в VELIQEN OS

В полном и ROM-комплектах неподписанный APK уже находится в `rom/payload/prebuilt/VELIQENLink.apk`. Архив исходников не содержит APK. После выполнения `bash android/BUILD-APK.sh --unsigned` интегратор может использовать файл из `android/build-output/`.

Аргументы интегратора и откат описаны в [rom/README-RU.md](../../rom/README-RU.md). Сначала выполняй проверку с `--dry-run`, используя реальные пути к дереву AOSP/LineageOS и нужному product makefile. Интегратор не прошивает устройство автоматически и не удаляет прежние приложения.

AOSP подписывает встроенное приложение ключом `platform` прошивки. Доступ `READ_CLIPBOARD_IN_BACKGROUND` требует соответствующей системной подписи или предусмотренной Android роли; одного помещения APK в `priv-app` недостаточно. Пользовательский root не требуется.

Публичное и системное приложения используют один ID, но могут иметь разные подписи. Публичный APK не обновляет приложение с другой платформенной подписью. Встроенное приложение обновляется вместе с ROM с сохранением её platform-ключа. Если прежний образ включает `org.kde.kdeconnect_tp`, миграцию состава образа нужно рассматривать отдельно.

## Подготовка выпуска и лицензии

[Руководство публикации](../GITHUB-RELEASE.md) описывает выпуск на GitHub. Установочные файлы размещаются в GitHub Releases, соответствующие исходники — в репозитории и Source-архиве. С бинарными файлами сохраняются доступ к соответствующему исходному коду, исходникам сторонних компонентов, лицензиям, уведомлениям об авторских правах и контрольным суммам.

GitHub Actions собирает DEB и неподписанный Android-файл без приватных ключей. Он не публикует выпуск автоматически. Локальные результаты не подтверждают успешное выполнение workflow: его нужно проверить в целевом репозитории.

См. [LICENSE](../../LICENSE), [THIRD_PARTY.md](../../THIRD_PARTY.md), [android/UPSTREAM.json](../../android/UPSTREAM.json), [android/CHANGES-VELIQEN.md](../../android/CHANGES-VELIQEN.md), `android/kdeconnect-android/COPYING`, `android/kdeconnect-android/LICENSES/` и `vendor/SOURCES.md`.

Приватные ключи личной и платформенной подписи не входят в соответствующий исходный код и не публикуются. Общедоступный `debug.keystore` upstream сохранён только для отдельного варианта development.
