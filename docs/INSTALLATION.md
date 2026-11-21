# Установка VELIQEN Link 1.2.1 / Installation

## Ubuntu

Файл: `veliqen-link_1.2.1_amd64.deb`. Целевая система: Ubuntu 26.04, amd64 (Intel/AMD), GNOME и PipeWire.

Открой терминал в папке с пакетом:

```bash
sudo apt update
sudo apt install ./veliqen-link_1.2.1_amd64.deb
```

APT скачает отсутствующие зависимости из подключённых репозиториев. Сам DEB содержит приложение и поставляемые с ним компоненты; копий всех пакетов Ubuntu внутри него нет. Для первой установки требуется интернет, если зависимости ещё не установлены. При недоступном репозитории APT не сможет скачать соответствующий пакет.

Обычный `dpkg -i` не скачивает зависимости. Не запускай `apt` из maintainer-скриптов и не удаляй системные lock-файлы APT. После обновления закрой старое окно приложения и открой VELIQEN Link снова.

В полном комплекте сохранён установщик с обходом недоступного регионального зеркала:

```bash
bash INSTALL-UBUNTU.sh --ubuntu-main-mirror
```

Запускай его от обычного пользователя. Он запросит `sudo`, предварительно загрузит зависимости, затем завершит прежнее окно текущего пользователя и установит пакет. Постоянные источники APT не меняются, сторонние репозитории не участвуют в этом запуске.

Для GNOME Wayland включи расширение VELIQEN Clipboard из настроек приложения. Если GNOME ещё не видит новое расширение, выйди из сеанса и войди снова. Автозапуск можно отключить в настройках приложения.

## Телефон / Phone

Открой `VELIQEN-Link-1.2.1-Android.apk` на телефоне и подтверди установку. При запросе Android разреши установку для того приложения, через которое открыт APK.

Public application ID: `tech.veliqen.link`. Release version code: `13706`.

Ранние тестовые версии использовали `org.kde.kdeconnect_tp` и общедоступный ключ разработки. Публичная версия получает отдельный ID и постоянную подпись. Она устанавливается рядом, поэтому удалять прежнее приложение и его данные заранее не требуется. В новой версии нужно заново подтвердить сопряжение и доступы. После перехода закрой или отключи прежнюю версию, чтобы обе копии не обрабатывали уведомления одновременно.

The public APK installs alongside earlier development builds. It does not import their private data. Pair it once and grant the desired permissions. Keep using the same release certificate for future updates. A platform-signed ROM build may require updates through the ROM if its certificate differs.

## Первое подключение / First connection

1. Подключи телефон и Ubuntu к одной локальной сети Wi-Fi.
2. Открой VELIQEN Link на обоих устройствах, выбери другое устройство и подтверди сопряжение.
3. На телефоне открой «Доступы» и включи нужные функции.
4. Для звонков отдельно подключи Bluetooth и разреши звук звонков.

Android запрашивает показ экрана для каждого сеанса. Разрешение на управление экраном включается отдельно. Приложение не может автоматически выдать себе системные разрешения.

## Удаление / Removal

Сначала заверши приложение в его настройках, затем:

```bash
sudo apt remove veliqen-link
```

Настройки сопряжения хранятся у пользователя. Удаление пакета не удаляет их автоматически. Ранее установленная пользователем копия расширения GNOME может оставаться в домашней папке. Настройка HFP, включённая отдельно, имеет отдельную инструкцию отката в `CALLS_RU.md`.

## References

- APT dependency resolution: https://manpages.ubuntu.com/manpages/resolute/man8/apt-get.8.html
- Android signing and update identity: https://developer.android.com/studio/publish/app-signing
- Feature behavior and platform limits: the corresponding source and `VALIDATION.md` in this release.
