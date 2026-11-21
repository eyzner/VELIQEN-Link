"""User-session integration; never run by package maintainer scripts.

Autostart changes and GNOME extension activation require a user action in the
application. No root account, other user's files or global GNOME preferences
are touched. A legacy extension is moved only when its complete file set and
hashes match our previously shipped version.
"""
from __future__ import annotations

import configparser
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


UUID = "veliqen-clipboard@veliqen.tech"
SYSTEM_EXTENSION = Path("/usr/share/gnome-shell/extensions") / UUID
LEGACY_EXTENSION = {
    "extension.js": "019733be95fd2fd2b959a7041985eb032ffff06b3be48fbfe68b7941717b2b16",
    "metadata.json": "de63b4e07bd625daa77e84984cd6c727fd6c191639a4a8c7ff65e25b7385e672",
}
AUTOSTART_TEMPLATE = """[Desktop Entry]
Type=Application
Name=VELIQEN Link
Comment=Local phone connection
Exec=veliqen-link --background
TryExec=veliqen-link
Icon=veliqen-link
Terminal=false
X-GNOME-Autostart-Delay=10
"""


def _xdg(name, fallback):
    path = Path(os.environ.get(name, str(Path.home() / fallback)))
    return path if path.is_absolute() else Path.home() / fallback


def autostart_path(config_home=None):
    return Path(config_home or _xdg("XDG_CONFIG_HOME", ".config")) / "autostart/veliqen-link.desktop"


def autostart_enabled(config_home=None):
    path = autostart_path(config_home)
    if not path.exists():
        # The standalone DEB ships the system XDG autostart entry.
        return True
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(path.read_text())
        section = parser["Desktop Entry"]
        return not section.getboolean("Hidden", fallback=False) and section.getboolean(
            "X-GNOME-Autostart-enabled", fallback=True)
    except (OSError, configparser.Error, ValueError, KeyError):
        return False


def set_autostart(enabled, config_home=None):
    if os.geteuid() == 0:
        raise RuntimeError("Измени автозапуск от обычного пользователя, без sudo.")
    path = autostart_path(config_home)
    if path.is_symlink():
        raise RuntimeError("Файл автозапуска является ссылкой. Он оставлен без изменений.")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = AUTOSTART_TEMPLATE + ("Hidden=false\nX-GNOME-Autostart-enabled=true\n" if enabled
        else "Hidden=true\nX-GNOME-Autostart-enabled=false\n")
    fd, temporary = tempfile.mkstemp(prefix=".veliqen-autostart-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate_legacy_extension(data_home=None, system_extension=SYSTEM_EXTENSION):
    """Return 'none', 'migrated' or 'custom'. Leave unknown overrides untouched."""
    system_extension = Path(system_extension)
    if not (system_extension / "extension.js").is_file():
        return "none"
    data = Path(data_home or _xdg("XDG_DATA_HOME", ".local/share"))
    user = data / "gnome-shell/extensions" / UUID
    if not user.exists() and not user.is_symlink():
        return "none"
    if user.is_symlink() or not user.is_dir():
        return "custom"
    children = list(user.iterdir())
    if {p.name for p in children} != set(LEGACY_EXTENSION):
        return "custom"
    for path in children:
        if path.is_symlink() or not path.is_file():
            return "custom"
        if hashlib.sha256(path.read_bytes()).hexdigest() != LEGACY_EXTENSION[path.name]:
            return "custom"
    backups = data / "veliqen-link/backups"
    backups.mkdir(parents=True, exist_ok=True, mode=0o700)
    backup = Path(tempfile.mkdtemp(prefix="gnome-user-", dir=backups))
    # Both paths are below XDG_DATA_HOME, so rename retains the exact old bytes.
    user.rename(backup / UUID)
    return "migrated"


def enable_clipboard_extension(*, settings=None, runner=None, data_home=None,
                               system_extension=SYSTEM_EXTENSION):
    """Enable only this extension, preserving all other GNOME extension choices.

    GNOME may discover a newly installed system extension only at next login.
    Save the enabled UUID for that login and return a clear status if the live
    shell cannot load it. Call this in a worker, never the GTK event loop.
    """
    if os.geteuid() == 0:
        raise RuntimeError("Включи буфер от обычного пользователя, без sudo.")
    if settings is None:
        if "GNOME" not in os.environ.get("XDG_CURRENT_DESKTOP", "").upper():
            raise RuntimeError("Дополнение буфера работает в сеансе GNOME. В этом сеансе можно передавать текст кнопкой «Буфер».")
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.gnome.shell", True) if source else None
        if schema is None:
            raise RuntimeError("Автопередача буфера поддерживается в GNOME и X11. Сейчас можно передавать текст кнопкой «Буфер».")
        settings = Gio.Settings.new_full(schema, None, None)
    if settings.get_boolean("disable-user-extensions"):
        raise RuntimeError("В GNOME отключены все расширения. Разреши их в приложении «Расширения», затем повтори включение буфера.")
    if not settings.is_writable("enabled-extensions") or not settings.is_writable("disabled-extensions"):
        raise RuntimeError("Настройки расширений GNOME заблокированы администратором.")
    migration = migrate_legacy_extension(data_home, system_extension)
    if migration == "custom":
        raise RuntimeError("Найдена изменённая пользовательская копия VELIQEN Clipboard. Она оставлена без изменений. Перенеси её в резервную папку и войди в Ubuntu заново, чтобы использовать дополнение из пакета.")
    enabled = settings.get_strv("enabled-extensions")
    disabled = settings.get_strv("disabled-extensions")
    if UUID in disabled and not settings.set_strv("disabled-extensions", [value for value in disabled if value != UUID]):
        raise RuntimeError("GNOME не сохранил настройку дополнения.")
    if UUID not in enabled and not settings.set_strv("enabled-extensions", [*enabled, UUID]):
        raise RuntimeError("GNOME не сохранил настройку дополнения.")
    if runner is None:
        from gi.repository import Gio
        Gio.Settings.sync()
    try:
        result = (runner or subprocess.run)(["gnome-extensions", "enable", UUID],
            capture_output=True, text=True, check=False, timeout=8)
        live = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        live = False
    if migration == "migrated":
        return "Прежнее дополнение сохранено в резервной копии. Выйди из сеанса Ubuntu и войди снова: GNOME загрузит дополнение из пакета."
    if live:
        return "Дополнение GNOME включено. Автопередача доступна в настройках буфера."
    return "Дополнение установлено и включено для следующего входа. Выйди из сеанса Ubuntu и войди снова."
