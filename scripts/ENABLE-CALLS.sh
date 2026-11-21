#!/usr/bin/env bash
# Optional user-session configuration. Never run by importing the application.
set -euo pipefail

action=""
restart_requested=0
for option in "$@"; do
    case "$option" in
        --apply|--restore)
            if [[ -n "$action" ]]; then
                echo "Выберите только одно действие: --apply или --restore." >&2
                exit 2
            fi
            action="$option"
            ;;
        --restart) restart_requested=1 ;;
        --help|-h) action=""; break ;;
        *) echo "Неизвестный параметр: $option" >&2; exit 2 ;;
    esac
done

if [[ -z "$action" ]]; then
    cat <<'HELP'
VELIQEN Link — необязательная настройка Bluetooth-звонков

  bash scripts/ENABLE-CALLS.sh --apply
      Сохранить прежний файл, выбрать native HFP backend и отключить
      автоматическую маршрутизацию только HFP-потоков телефона.
      Микрофон и динамики затем выбираются в VELIQEN Link.

  bash scripts/ENABLE-CALLS.sh --apply --restart
      Дополнительно перезапустить WirePlumber текущего пользователя.
      Это временно прервет звук. Запускайте после завершения разговора.

  bash scripts/ENABLE-CALLS.sh --restore --restart
      Восстановить предыдущий файл и перезапустить WirePlumber.

Требуются WirePlumber 0.5+ и PipeWire с native Telephony API (1.4+).
Скрипт не сопрягает устройства и не начинает звонки. Роли Bluetooth,
глобальные настройки и системные файлы не переписываются.
HELP
    exit 0
fi

if [[ "${EUID:-$(id -u)}" -eq 0 ]]; then
    echo "Запустите скрипт от обычного пользователя Ubuntu, без sudo." >&2
    exit 1
fi
command -v python3 >/dev/null || { echo "Нужен python3." >&2; exit 1; }
if [[ "$action" == "--apply" ]]; then
    command -v wireplumber >/dev/null || { echo "WirePlumber не установлен." >&2; exit 1; }
    wireplumber_version="$(wireplumber --version)"
    python3 - "$wireplumber_version" <<'PY'
import re, sys
match = re.search(r"\b(\d+)\.(\d+)(?:\.\d+)?", sys.argv[1])
if not match or tuple(map(int, match.groups())) < (0, 5):
    raise SystemExit("Нужен WirePlumber 0.5+. Старые Lua-конфигурации этот скрипт не изменяет.")
PY
fi

python3 - "$action" <<'PY'
import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

root = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home()/".config")))
if not root.is_absolute():
    root = Path.home()/".config"
config = root/"wireplumber"/"wireplumber.conf.d"/"55-veliqen-link.conf"
state = root/"veliqen-link"/"wireplumber-backup.json"
payload = b'''# Managed by VELIQEN Link. Restore with scripts/ENABLE-CALLS.sh --restore
monitor.bluez.properties = {
  bluez5.hfphsp-backend = "native"
}

# Match only phone Audio Gateway streams. Headset and A2DP music rules stay intact.
monitor.bluez.rules = [
  {
    matches = [ { api.bluez5.profile = "headset-audio-gateway" } ]
    actions = { update-props = { node.autoconnect = false } }
  }
]
'''

def digest(data):
    return hashlib.sha256(data).hexdigest()

def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".veliqen-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)

if config.is_symlink() or state.is_symlink():
    raise SystemExit("Файл настройки или состояние — символическая ссылка. Автоматическая замена отменена.")
record = json.loads(state.read_text()) if state.exists() else None
if record and config.exists() and digest(config.read_bytes()) != record["written_sha256"]:
    raise SystemExit("Файл WirePlumber изменен после настройки VELIQEN Link. Он сохранен без изменений. Резервная копия: " + str(record.get("backup") or "исходного файла не было"))

if sys.argv[1] == "--apply":
    config.parent.mkdir(parents=True, exist_ok=True)
    if record is None:
        backup = None
        if config.exists():
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup = config.with_name(config.name + ".before-" + stamp)
            atomic_write(backup, config.read_bytes())
        record = {"backup": str(backup) if backup else None,
                  "written_sha256": digest(payload)}
        # Save the recovery record before replacing the existing config.
        atomic_write(state, json.dumps(record, ensure_ascii=False, indent=2).encode())
    atomic_write(config, payload)
    record["written_sha256"] = digest(payload)
    atomic_write(state, json.dumps(record, ensure_ascii=False, indent=2).encode())
    print("Настройка сохранена:", config)
    if record["backup"]:
        print("Резервная копия:", record["backup"])
else:
    if record is None:
        raise SystemExit("Сохраненной настройки VELIQEN Link нет; ничего не изменено.")
    if record["backup"]:
        backup = Path(record["backup"])
        if backup.parent != config.parent or not backup.name.startswith(config.name + ".before-") or backup.is_symlink():
            raise SystemExit("Путь резервной копии не прошел проверку. Ничего не изменено.")
        atomic_write(config, backup.read_bytes())
    else:
        config.unlink(missing_ok=True)
    state.unlink()
    print("Прежняя настройка восстановлена.")
PY

if [[ "$restart_requested" -eq 1 ]]; then
    systemctl --user restart wireplumber
    echo "WirePlumber перезапущен. Переподключите телефон в Bluetooth."
else
    echo "Для применения после завершения звонков: systemctl --user restart wireplumber"
fi
