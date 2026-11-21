# VELIQEN Link 1.2.1 — комплект r2

## Русский

VELIQEN Link связывает Ubuntu и VELIQEN OS / Android: SMS, файлы, текст буфера, экран телефона и сотовые звонки через Bluetooth.

### Исправлены инструкции и папки

- Пользовательские инструкции на русском и английском описывают установку готовых файлов, подключение и использование.
- Готовые DEB и APK находятся в папке **Установочные-файлы** полного комплекта.
- Установщики используют новое название папки; DEB содержит обновлённую инструкцию.
- Версия приложений остаётся **1.2.1**. Отметка **r2** относится к оформлению и содержимому комплекта.

### Установка

- Ubuntu 26.04 amd64: `veliqen-link_1.2.1_amd64.deb`. В папке с файлом выполни `sudo apt install ./veliqen-link_1.2.1_amd64.deb`. APT скачает необходимые зависимости.
- Телефон: открой `VELIQEN-Link-1.2.1-Android.apk` и подтверди установку.
- Подключи устройства к одной сети Wi-Fi, подтверди сопряжение и нужные разрешения. Для сотовых звонков отдельно подключи Bluetooth.

### В этом выпуске

- Самостоятельный DEB включает приложение, расширение GNOME, автозапуск и scrcpy 5.0.1 для дополнительного режима экрана.
- Публичный APK использует постоянную release-подпись и идентификатор `tech.veliqen.link`.
- Сохранён упрощённый интерфейс с номеронабирателем, DTMF, SMS и быстрыми действиями телефона.
- В комплект входят исходники, лицензии, контрольные суммы и презентации на русском и английском.

### Переход с тестовых сборок

Новый APK устанавливается рядом с ранним приложением `org.kde.kdeconnect_tp`. Данные старого приложения не удаляются. Открой новую копию, подтверди сопряжение и доступы, затем закрой или отключи прежнюю, чтобы не получать дубли уведомлений. Следующие публичные обновления используют постоянную подпись.

### Условия работы

APT требует доступных репозиториев для отсутствующих зависимостей. Для автоматического буфера в GNOME Wayland нужно включить расширение, после первой установки может понадобиться повторный вход в сеанс. Обычный APK не обходит ограничения Android на фоновый буфер. Android запрашивает показ экрана для каждого сеанса. Сотовые звонки требуют совместимого Bluetooth HFP и работающей телефонии на самом телефоне.

Объём выполненных проверок и ограничения описаны в `docs/VALIDATION.md`. Исходники и уведомления компонентов включены в `VELIQEN-Link-1.2.1-Source.zip`.

## English

VELIQEN Link connects Ubuntu and a VELIQEN OS / Android phone for SMS, files, clipboard text, phone screen access and cellular calls through Bluetooth.

### Updated instructions and folders

- Russian and English user guides cover installation, pairing and everyday use.
- Ready-to-install DEB and APK files are in the **Установочные-файлы** (Installers) folder of the full kit.
- Installation scripts use the new folder name; the DEB includes the updated user guide.
- The application version remains **1.2.1**. The **r2** label identifies this revised distribution kit.

### Install

- Ubuntu 26.04 amd64: run `sudo apt install ./veliqen-link_1.2.1_amd64.deb` in the download folder. APT resolves the required system dependencies.
- Phone: open `VELIQEN-Link-1.2.1-Android.apk` and confirm installation.
- Join the same Wi-Fi network, confirm pairing and grant the selected permissions. Pair Bluetooth separately for cellular call audio.

### Changes

The standalone DEB includes the desktop app, GNOME extension, autostart and scrcpy 5.0.1 for the optional advanced screen mode. The public APK uses a permanent release certificate and the `tech.veliqen.link` application ID. The simplified interface includes a dial pad, DTMF, SMS conversations and phone shortcuts. Source, licenses, checksums and Russian/English presentations accompany the release.

The APK installs alongside earlier development builds that used `org.kde.kdeconnect_tp`. Pair the new app and grant its permissions once. Close or disable the old copy to avoid duplicate notifications. Its private data is not removed or migrated automatically.

GNOME Wayland clipboard capture needs the included extension and may require a new login after installation. Regular APKs remain subject to Android background clipboard restrictions. Screen sharing requires consent each session. Cellular calls depend on Bluetooth HFP and the phone's working telephony stack.

See `docs/VALIDATION.md` for the tested scope. Corresponding source and component notices are included in the Source ZIP.
