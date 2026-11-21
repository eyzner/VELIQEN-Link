#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
if [[ "$(uname -m)" != x86_64 ]]; then
    printf '%s\n' 'В комплекте scrcpy для x86_64. На другой архитектуре установи scrcpy и adb из репозитория системы.'
    exit 0
fi
ARCHIVE="$ROOT/vendor/scrcpy-linux-x86_64-v5.0.1.tar.gz"
if [[ ! -f "$ARCHIVE" ]]; then
    printf '%s\n' 'Не найден архив scrcpy. Нужен полный комплект VELIQEN Link.'
    exit 1
fi
EXPECTED=9f969d30cc574816077edecda65719c1c70b0aee1df1ba5fa00779ac07b8fd6e
ACTUAL="$(sha256sum "$ARCHIVE")"
if [[ "${ACTUAL%% *}" != "$EXPECTED" ]]; then
    printf '%s\n' 'Контрольная сумма scrcpy не совпала. Установка остановлена.'
    exit 1
fi
TARGET="$HOME/.local/lib/veliqen-link/scrcpy"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/veliqen-scrcpy.XXXXXX")"
trap 'rm -rf -- "$STAGE"' EXIT
tar -xzf "$ARCHIVE" -C "$STAGE"
SOURCE="$STAGE/scrcpy-linux-x86_64-v5.0.1"
if [[ ! -x "$SOURCE/scrcpy" || ! -f "$SOURCE/scrcpy-server" ]]; then
    printf '%s\n' 'Структура официального архива scrcpy отличается от ожидаемой.'
    exit 1
fi
mkdir -p "$TARGET"
cp -a "$SOURCE/." "$TARGET/"
printf '%s\n' 'scrcpy 5.0.1 установлен для текущего пользователя.'
