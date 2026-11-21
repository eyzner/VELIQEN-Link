#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
umask 077
HERE=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
PROJECT="$HERE/kdeconnect-android"
VERSION=1.2.1
MODE=${1:---release}
case "$MODE" in
    --release|--development|--unsigned|--personal) shift || true ;;
    *) printf 'Использование: bash BUILD-APK.sh [--release|--unsigned|--development] [аргументы Gradle]\n' >&2; exit 2 ;;
esac
[[ "$MODE" != --personal ]] || MODE=--release
command -v java >/dev/null || { printf 'Нужен JDK 21.\n' >&2; exit 1; }
command -v javac >/dev/null || { printf 'Нужен полный JDK 21 (javac), не только JRE.\n' >&2; exit 1; }
SDK_PATH=${ANDROID_HOME:-${ANDROID_SDK_ROOT:-}}
if [[ -z "$SDK_PATH" && -f "$PROJECT/local.properties" ]]; then
    SDK_PATH=$(python3 - "$PROJECT/local.properties" <<'PY'
import sys
from pathlib import Path
for line in Path(sys.argv[1]).read_text().splitlines():
    if line.startswith('sdk.dir='):
        print(line.split('=', 1)[1].replace('\\:', ':').replace('\\ ', ' '))
        break
PY
)
fi
[[ -d "$SDK_PATH" ]] || { printf 'Задайте ANDROID_HOME: каталог установленного Android SDK.\n' >&2; exit 1; }
export ANDROID_HOME="$SDK_PATH"
APKSIGNER="$SDK_PATH/build-tools/37.0.0/apksigner"
[[ -x "$APKSIGNER" ]] || { printf 'Установите Android SDK build-tools;37.0.0.\n' >&2; exit 1; }
mkdir -p "$HERE/build-output"
if [[ "$MODE" == --release ]]; then
    if [[ -z "${VELIQEN_KEYSTORE:-}" ]]; then
        SIGNING_DIR=${VELIQEN_SIGNING_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/veliqen-link/signing}
        VELIQEN_KEYSTORE="$SIGNING_DIR/veliqen-link-release.p12"
        PASSWORD_FILE="$SIGNING_DIR/store-password"
        [[ -f "$VELIQEN_KEYSTORE" && -f "$PASSWORD_FILE" ]] || {
            printf 'Нет постоянного ключа выпуска. Восстановите резервную копию в %s.\nДля НОВОГО проекта ключ создаётся отдельно: bash INIT-SIGNING.sh\n' "$SIGNING_DIR" >&2
            exit 1
        }
        VELIQEN_STORE_PASSWORD=$(cat "$PASSWORD_FILE")
    fi
    : "${VELIQEN_STORE_PASSWORD:?Задайте VELIQEN_STORE_PASSWORD}"
    [[ -f "$VELIQEN_KEYSTORE" ]] || { printf 'Ключ не найден.\n' >&2; exit 1; }
    export VELIQEN_KEYSTORE VELIQEN_STORE_PASSWORD
    export VELIQEN_KEY_ALIAS=${VELIQEN_KEY_ALIAS:-veliqen-link}
    export VELIQEN_KEY_PASSWORD=${VELIQEN_KEY_PASSWORD:-$VELIQEN_STORE_PASSWORD}
fi
GRADLE_OPTIONS=()
[[ "$MODE" != --development ]] || GRADLE_OPTIONS+=(-PveliqenDevelopment=true)
cd "$PROJECT"
# Build unsigned first. This also keeps the ROM payload independent of the public key.
env -u VELIQEN_KEYSTORE -u VELIQEN_STORE_PASSWORD -u VELIQEN_KEY_ALIAS -u VELIQEN_KEY_PASSWORD \
    ./gradlew --no-daemon assembleVeliqen "${GRADLE_OPTIONS[@]}" "$@"
APK=$(python3 - "$PROJECT/build/outputs/apk/veliqen" <<'PY'
import json, sys
from pathlib import Path
root=Path(sys.argv[1])
metadata=json.loads((root/'output-metadata.json').read_text())
element,=metadata['elements']
if element['versionCode'] != 13706:
    sys.exit('Версия build output не соответствует 1.2.1.')
print(root/element['outputFile'])
PY
)
case "$MODE" in
    --development)
        OUT="$HERE/build-output/VELIQEN-Link-$VERSION-development.apk"
        "$APKSIGNER" sign --ks "$PROJECT/debug.keystore" --ks-key-alias androiddebugkey \
            --ks-pass pass:android --key-pass pass:android --out "$OUT" "$APK"
        printf 'Development APK: отдельный ID tech.veliqen.link.development и публичный отладочный ключ.\n'
        ;;
    --release)
        OUT="$HERE/build-output/VELIQEN-Link-$VERSION-release.apk"
        "$APKSIGNER" sign --ks "$VELIQEN_KEYSTORE" --ks-key-alias "$VELIQEN_KEY_ALIAS" \
            --ks-pass env:VELIQEN_STORE_PASSWORD --key-pass env:VELIQEN_KEY_PASSWORD --out "$OUT" "$APK"
        cp -- "$APK" "$HERE/build-output/VELIQEN-Link-$VERSION-unsigned.apk"
        printf 'Создан APK с постоянной подписью выпуска.\n'
        ;;
    --unsigned)
        OUT="$HERE/build-output/VELIQEN-Link-$VERSION-unsigned.apk"
        cp -- "$APK" "$OUT"
        printf 'Создан unsigned APK для подписи при сборке прошивки.\n'
        ;;
esac
if [[ "$MODE" != --unsigned ]]; then
    "$APKSIGNER" verify --verbose --print-certs "$OUT"
fi
"$SDK_PATH/build-tools/37.0.0/zipalign" -c -P 16 4 "$OUT"
if [[ "$MODE" == --release ]]; then
    python3 "$HERE/VERIFY-APK.py" "$OUT" --sdk "$SDK_PATH" --report "$HERE/build-output/RELEASE-VERIFY.json"
    UNSIGNED="$HERE/build-output/VELIQEN-Link-$VERSION-unsigned.apk"
    chmod 644 "$UNSIGNED"
    (cd "$HERE/build-output" && sha256sum "$(basename -- "$UNSIGNED")" > "$(basename -- "$UNSIGNED").sha256")
    chmod 644 "$UNSIGNED.sha256"
fi
chmod 644 "$OUT"
rm -f -- "$OUT.idsig"
(cd "$(dirname -- "$OUT")" && sha256sum "$(basename -- "$OUT")" > "$(basename -- "$OUT").sha256")
chmod 644 "$OUT.sha256"
printf 'APK: %s\n' "$OUT"
