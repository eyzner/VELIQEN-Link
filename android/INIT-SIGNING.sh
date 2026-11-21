#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Explicit one-time generation. Never call this in CI for an existing release.
set -euo pipefail
umask 077
SIGNING_DIR=${VELIQEN_SIGNING_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/veliqen-link/signing}
command -v keytool >/dev/null || { printf 'Нужен JDK 21 (keytool).\n' >&2; exit 1; }
command -v python3 >/dev/null || { printf 'Нужен Python 3.\n' >&2; exit 1; }
mkdir -p "$SIGNING_DIR"
chmod 700 "$SIGNING_DIR"
KEYSTORE="$SIGNING_DIR/veliqen-link-release.p12"
PASSWORD_FILE="$SIGNING_DIR/store-password"
if [[ -e "$KEYSTORE" || -e "$PASSWORD_FILE" ]]; then
    printf 'Ключ или пароль уже существуют. Сохранённые файлы не изменены: %s\n' "$SIGNING_DIR" >&2
    exit 1
fi
python3 - "$PASSWORD_FILE" <<'PY'
import os, secrets, sys
fd = os.open(sys.argv[1], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write(secrets.token_urlsafe(48))
PY
VELIQEN_STORE_PASSWORD=$(cat "$PASSWORD_FILE")
export VELIQEN_STORE_PASSWORD
keytool -genkeypair -keystore "$KEYSTORE" -storetype PKCS12 \
    -storepass:env VELIQEN_STORE_PASSWORD -keypass:env VELIQEN_STORE_PASSWORD \
    -alias veliqen-link -keyalg RSA -keysize 3072 -validity 10000 \
    -dname "CN=VELIQEN Link Release, O=VELIQEN" -noprompt
chmod 600 "$KEYSTORE" "$PASSWORD_FILE"
printf 'Создан постоянный ключ: %s\nСохраните отдельную резервную копию каталога. Не публикуйте ключ и store-password.\n' "$SIGNING_DIR"
