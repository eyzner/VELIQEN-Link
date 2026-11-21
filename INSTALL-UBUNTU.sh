#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
NO_GNOME=0
NO_AUTOSTART=0
UBUNTU_MAIN_MIRROR=0
KIT="$ROOT"
while (($#)); do
    case "$1" in
        --no-gnome) NO_GNOME=1; shift ;;
        --no-autostart) NO_AUTOSTART=1; shift ;;
        --ubuntu-main-mirror) UBUNTU_MAIN_MIRROR=1; shift ;;
        --kit)
            if (($# < 2)) || [[ -z "$2" ]]; then
                printf '%s\n' 'После --kit укажи каталог распакованного комплекта.' >&2
                exit 2
            fi
            KIT="$2"; shift 2 ;;
        --help)
            printf '%s\n' 'Установка VELIQEN Link и зависимостей в Ubuntu/Debian.' \
                'Запускать от обычного пользователя.' \
                '  --kit DIR              Распакованный комплект с каталогом artifacts.' \
                '  --ubuntu-main-mirror   Временно использовать официальные HTTPS-серверы Ubuntu.' \
                '                         Файлы /etc/apt не меняются; сторонние репозитории не используются.' \
                '  --no-gnome             Совместимость: дополнение не включается установщиком.' \
                '  --no-autostart         Выключить автозапуск для текущего пользователя.'
            exit 0 ;;
        *) printf 'Неизвестный параметр: %s\n' "$1" >&2; exit 2 ;;
    esac
done
if [[ "$EUID" -eq 0 ]]; then
    printf '%s\n' 'Запусти установщик от своего обычного пользователя. Он сам запросит sudo для пакетов.'
    exit 1
fi
if ! command -v apt-get >/dev/null 2>&1; then
    printf '%s\n' 'Этот установщик предназначен для Ubuntu/Debian.'
    exit 1
fi
if ! command -v sudo >/dev/null 2>&1 || [[ ! -x /usr/bin/python3 ]]; then
    printf '%s\n' 'Для установки нужны sudo и системный Python 3.' >&2
    exit 1
fi
if [[ ! -d "$KIT" ]]; then
    printf 'Не найден каталог комплекта: %s\n' "$KIT" >&2
    exit 1
fi
ROOT="$(cd -- "$KIT" && pwd)"
PACKAGE="$ROOT/artifacts/veliqen-link_1.2.1_amd64.deb"
if [[ ! -f "$PACKAGE" ]]; then
    printf '%s\n' 'Не найден .deb пакет. Нужен полный комплект либо предварительный запуск BUILD-PACKAGES.py.'
    exit 1
fi
for REQUIRED_FILE in scripts/STOP-RUNNING.py linux/veliqen_link/session_setup.py; do
    if [[ ! -f "$ROOT/$REQUIRED_FILE" ]]; then
        printf 'В комплекте отсутствует файл: %s\n' "$REQUIRED_FILE" >&2
        exit 1
    fi
done

# APT must be able to read the local package without traversing a private home.
WORK_DIR="$(mktemp -d /tmp/veliqen-link-install.XXXXXXXX)"
cleanup() {
    local result=$?
    trap - EXIT INT TERM
    if [[ "${WORK_DIR%/*}" == /tmp && "${WORK_DIR##*/}" =~ ^veliqen-link-install\.[[:alnum:]]+$ && -d "$WORK_DIR" && ! -L "$WORK_DIR" && -O "$WORK_DIR" ]]; then
        rm -rf -- "$WORK_DIR" 2>/dev/null || sudo -n /bin/rm -rf -- "$WORK_DIR" >/dev/null 2>&1 || true
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
chmod 0755 "$WORK_DIR"
mkdir -m 0755 "$WORK_DIR/lists" "$WORK_DIR/archives" "$WORK_DIR/sources.list.d"
STAGED_PACKAGE="$WORK_DIR/veliqen-link_1.2.1_amd64.deb"
install -m 0644 -- "$PACKAGE" "$STAGED_PACKAGE"
APT_OPTIONS=(
    -o "Acquire::Retries=1"
    -o "Acquire::http::Timeout=15"
    -o "Acquire::https::Timeout=15"
    -o "APT::Update::Error-Mode=any"
    -o "Dir::State::lists=$WORK_DIR/lists"
    -o "Dir::Cache::archives=$WORK_DIR/archives"
    -o "Dir::Cache::pkgcache=$WORK_DIR/pkgcache.bin"
    -o "Dir::Cache::srcpkgcache=$WORK_DIR/srcpkgcache.bin"
)
if [[ "$UBUNTU_MAIN_MIRROR" -eq 1 ]]; then
    printf '%s\n' 'Готовим временные официальные источники Ubuntu. Настройки /etc/apt останутся прежними…'
    /usr/bin/python3 - /etc/apt /etc/os-release /usr/share/keyrings/ubuntu-archive-keyring.gpg "$WORK_DIR/sources.list.d" "$(dpkg --print-architecture)" <<'PY'
import pathlib
import re
import shlex
import sys
from urllib.parse import urlsplit

apt_dir, os_file, keyring, destination = map(pathlib.Path, sys.argv[1:5])
architecture = sys.argv[5]

def fail(message):
    raise ValueError(message)

def canonical_uri(uri):
    parsed = urlsplit(uri)
    host = (parsed.hostname or "").lower()
    if host in {"ports.ubuntu.com", "old-releases.ubuntu.com"}:
        fail("Этот тип архива Ubuntu не поддерживается параметром --ubuntu-main-mirror.")
    if host not in {"archive.ubuntu.com", "security.ubuntu.com"} and not re.fullmatch(r"[a-z]{2}\.archive\.ubuntu\.com", host):
        return None
    if (parsed.scheme not in {"http", "https"} or parsed.path.rstrip("/") != "/ubuntu"
            or parsed.username or parsed.password or parsed.port is not None or parsed.query or parsed.fragment):
        fail("Нестандартный адрес официального архива Ubuntu; автоматическая замена остановлена.")
    server = "security.ubuntu.com" if host == "security.ubuntu.com" else "archive.ubuntu.com"
    return "https://" + server + "/ubuntu"

def validate(suites, components, fields):
    if not suites or any(s not in allowed_suites for s in suites):
        fail("Активные источники Ubuntu содержат другую версию системы или нестандартный Suite.")
    if not components or any(c not in {"main", "restricted", "universe", "multiverse"} for c in components):
        fail("Нестандартные Components в источниках Ubuntu; автоматическая замена остановлена.")
    for name in ("trusted", "allow-insecure", "allow-weak", "allow-downgrade-to-insecure"):
        if name in fields and fields[name].strip().lower() not in {"no", "false", "0"}:
            fail("В источнике Ubuntu отключена проверка доверия; такой источник не будет использован.")

def parse_fields(block):
    fields = {}
    current = None
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0].isspace() and current:
            fields[current] += "\n" + line.strip()
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9-]*):\s*(.*)", line)
        if not match or match[1].lower() in fields:
            fail("Не удалось разобрать формат .sources; автоматическая замена остановлена.")
        current = match[1].lower()
        fields[current] = match[2]
    return fields

try:
    release = {}
    for line in os_file.read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            release[name] = " ".join(shlex.split(value))
    if release.get("ID") != "ubuntu":
        fail("Параметр --ubuntu-main-mirror предназначен только для Ubuntu.")
    codename = release.get("VERSION_CODENAME") or release.get("UBUNTU_CODENAME", "")
    if not re.fullmatch(r"[a-z]+", codename) or architecture not in {"amd64", "i386"}:
        fail("Версия или архитектура Ubuntu не поддерживает этот способ замены зеркала.")
    if not keyring.is_file():
        fail("Не найден установленный ubuntu-archive-keyring. Проверка подписей не будет отключена.")
    allowed_suites = {codename + suffix for suffix in ("", "-updates", "-security", "-backports", "-proposed")}
    files = [apt_dir / "sources.list"]
    files += sorted(p for p in (apt_dir / "sources.list.d").glob("*") if p.suffix in {".list", ".sources"})
    count = 0
    for source in files:
        if not source.is_file():
            continue
        content = source.read_text()
        if source.suffix == ".sources":
            output = []
            for block in re.split(r"\n\s*\n", content):
                fields = parse_fields(block)
                if fields.get("enabled", "yes").strip().lower() == "no" or "deb" not in fields.get("types", "").split():
                    continue
                uris = [canonical_uri(uri) for uri in fields.get("uris", "").split()]
                if not any(uris):
                    continue
                if None in uris or not uris:
                    fail("Один .sources-блок смешивает официальные и другие адреса. Автоматическая замена остановлена.")
                validate(fields.get("suites", "").split(), fields.get("components", "").split(), fields)
                rewritten = []
                replacing_uri = False
                for line in block.splitlines():
                    match = re.match(r"^([A-Za-z][A-Za-z0-9-]*):", line)
                    if match:
                        replacing_uri = match[1].lower() == "uris"
                        rewritten.append("URIs: " + " ".join(dict.fromkeys(uris)) if replacing_uri else line)
                    elif not (replacing_uri and line[:1].isspace() and not line.lstrip().startswith("#")):
                        rewritten.append(line)
                if "signed-by" not in fields:
                    rewritten.append("Signed-By: " + str(keyring))
                output.append("\n".join(rewritten).strip())
                count += 1
            suffix = ".sources"
            rendered = "\n\n".join(output)
        else:
            output = []
            for line in content.splitlines():
                active, _, comment = line.partition("#")
                if not active.strip() or active.lstrip().startswith("deb-src "):
                    continue
                match = re.fullmatch(r"\s*deb\s+(?:\[([^\]]*)\]\s+)?(\S+)\s+(\S+)\s+(.+?)\s*", active)
                if not match:
                    if "ubuntu.com" in active:
                        fail("Нестандартная строка Ubuntu в .list; автоматическая замена остановлена.")
                    continue
                uri = canonical_uri(match[2])
                if uri is None:
                    continue
                options = (match[1] or "").split()
                option_fields = dict(option.lower().split("=", 1) for option in options if "=" in option)
                validate([match[3]], match[4].split(), option_fields)
                if "signed-by" not in option_fields:
                    options.append("signed-by=" + str(keyring))
                output.append("deb [" + " ".join(options) + "] " + uri + " " + match[3] + " " + match[4] + (" #" + comment if comment else ""))
                count += 1
            suffix = ".list"
            rendered = "\n".join(output)
        if rendered:
            target = destination / ("ubuntu-" + str(files.index(source)) + suffix)
            target.write_text(rendered + "\n")
            target.chmod(0o644)
    if not count:
        fail("Не найдены активные стандартные источники Ubuntu. Автоматическая замена зеркала невозможна.")
except (OSError, ValueError) as exc:
    sys.exit("Не удалось подготовить временные источники: " + str(exc))
print("Временные HTTPS-источники Ubuntu готовы; проверка подписей APT сохранена.")
PY
    : > "$WORK_DIR/sources.list"
    chmod 0644 "$WORK_DIR/sources.list"
    APT_OPTIONS+=(
        -o "Dir::Etc::sourcelist=$WORK_DIR/sources.list"
        -o "Dir::Etc::sourceparts=$WORK_DIR/sources.list.d"
    )
fi
printf '%s\n' 'Обновляем списки и загружаем зависимости перед установкой…'
sudo apt-get "${APT_OPTIONS[@]}" update
sudo apt-get "${APT_OPTIONS[@]}" --download-only --no-remove install -y "$STAGED_PACKAGE"
printf '%s\n' 'Пакеты загружены. Завершаем прежнее окно VELIQEN Link…'
/usr/bin/python3 "$ROOT/scripts/STOP-RUNNING.py"
printf '%s\n' 'Устанавливаем приложение и подготовленные зависимости…'
# Keep the acquisition phase enabled for a local .deb. APT --no-download can
# leave the volatile archive filename relative, even with an absolute argument,
# and fail before dpkg. The unchanged archive cache reuses downloaded packages.
sudo apt-get "${APT_OPTIONS[@]}" --no-remove install -y "$STAGED_PACKAGE"
if [[ "$NO_AUTOSTART" -eq 1 ]]; then
    PYTHONPATH="$ROOT/linux" /usr/bin/python3 -c 'from veliqen_link.session_setup import set_autostart; set_autostart(False)'
fi
printf '\n%s\n' 'Установка завершена. Открой VELIQEN Link из меню приложений.' 'На телефон установи artifacts/VELIQEN-Link-1.2.1-Android.apk.' 'Первое сопряжение: одна сеть Wi-Fi → Подключить → подтвердить на телефоне.' 'Звонки: открой раздел Звонки → Подключить звонки.' 'Экран: нажми Открыть экран телефона и подтверди запрос на телефоне.' 'Буфер GNOME: включи автопередачу в Настройках. После первой установки может понадобиться повторный вход в Ubuntu.'

if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
    nohup veliqen-link >/dev/null 2>&1 </dev/null &
fi
