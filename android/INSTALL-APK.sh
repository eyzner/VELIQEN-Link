#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
HERE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
APK=${1:-$HERE/../Установочные-файлы/VELIQEN-Link-1.2.1-Android.apk}
[[ -f "$APK" ]] || { printf 'APK не найден: %s\nСкачайте готовый VELIQEN-Link-1.2.1-Android.apk или укажите путь к нему первым аргументом.\n' "$APK" >&2; exit 1; }
command -v adb >/dev/null || { printf 'Установите Android platform-tools (adb).\n' >&2; exit 1; }
printf 'Подтвердите отладку на вашем телефоне, если появится запрос.\n'
if ! adb install -r "$APK"; then
    printf '\nУстановка не выполнена. Проверьте вывод adb. Обновления tech.veliqen.link требуют того же ключа выпуска.\nПриложение не удалено; его данные не затронуты этим скриптом.\n' >&2
    exit 1
fi
