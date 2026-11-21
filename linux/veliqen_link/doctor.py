"""Diagnostics contain versions and availability, never message or clipboard data."""
import os
import platform
import shutil
import subprocess
from .screen import ScreenManager
from . import __version__

def report():
    lines = [f"VELIQEN Link {__version__}", f"Система: {platform.system()} {platform.release()}",
             f"Сеанс: {os.environ.get('XDG_SESSION_TYPE', 'не определён')}",
             f"Рабочий стол: {os.environ.get('XDG_CURRENT_DESKTOP', 'не определён')}", ""]
    for name in ["python3", "kdeconnect-cli", "kdeconnectd", "adb", "scrcpy", "pactl", "pw-dump", "pw-link", "bluetoothctl", "gnome-extensions"]:
        lines.append(f"{name}: {'есть' if shutil.which(name) else 'не найден в PATH'}")
    screen = ScreenManager()
    for name in ("scrcpy", "adb"):
        lines.append(f"{name}, выбранный приложением: {screen._find_tool(name) or 'не найден'}")
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk, Gio, GLib
        lines.append(f"GTK: {Gtk.get_major_version()}.{Gtk.get_minor_version()}")
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        for name in ["org.kde.kdeconnect", "org.pipewire.Telephony", "tech.veliqen.Link.Clipboard"]:
            result = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)",(name,)), GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE, 1500, None)
            lines.append(f"{name}: {'работает' if result.unpack()[0] else 'не запущен'}")
    except Exception as exc:
        lines.append("Сеанс D-Bus/GTK недоступен: " + type(exc).__name__)
    lines += ["", "Отсутствие Bluetooth-службы в этой проверке не означает неисправность телефона.",
              "Звонки и звук проверяются после сопряжения на реальном оборудовании."]
    return "\n".join(lines)
