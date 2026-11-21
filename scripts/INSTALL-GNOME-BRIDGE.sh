#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
UUID="veliqen-clipboard@veliqen.tech"
SOURCE="$ROOT/gnome-extension/$UUID"
if ! command -v gnome-shell >/dev/null 2>&1; then
    printf '%s\n' 'GNOME не обнаружен. Расширение буфера не требуется для X11.'
    exit 0
fi
if [[ ! -f "$SOURCE/extension.js" ]]; then
    printf '%s\n' 'Не найдено расширение. Запускай скрипт из полного распакованного комплекта.'
    exit 1
fi
TARGET="${XDG_DATA_HOME:-$HOME/.local/share}/gnome-shell/extensions/$UUID"
mkdir -p "$(dirname "$TARGET")"
if [[ -d "$TARGET" ]]; then
    BACKUP="${XDG_DATA_HOME:-$HOME/.local/share}/veliqen-link/backups/gnome-$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$BACKUP"
    cp -a "$TARGET" "$BACKUP/"
fi
mkdir -p "$TARGET"
cp "$SOURCE/extension.js" "$SOURCE/metadata.json" "$TARGET/"
if command -v gnome-extensions >/dev/null 2>&1; then
    if gnome-extensions enable "$UUID"; then
        printf '%s\n' 'Расширение VELIQEN Clipboard включено.'
    else
        printf '%s\n' 'Расширение установлено. Выйди из сеанса Ubuntu и войди снова, затем выполни:'
        printf 'gnome-extensions enable %q\n' "$UUID"
    fi
fi
printf '%s\n' 'Автоматическую синхронизацию включи в VELIQEN Link → Настройки.'
