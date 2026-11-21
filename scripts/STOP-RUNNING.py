#!/usr/bin/python3
"""Close only the current user's running VELIQEN Link before an upgrade.

Uses the session D-Bus owner and a Linux pidfd. No process-name search, pkill,
SIGKILL, fallback to an unverified PID, or mutation of desktop settings.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from pathlib import Path
import os
import select
import signal
import sys
import time

SERVICE = "tech.veliqen.Link"


class StopError(RuntimeError):
    pass


class NoSession(RuntimeError):
    pass


class SessionBus:
    def __init__(self):
        try:
            from gi.repository import Gio, GLib
        except (ImportError, ValueError) as exc:
            raise NoSession("Компоненты предыдущего приложения не установлены; закрывать его не требуется.") from exc
        self.Gio, self.GLib = Gio, GLib
        # Never autolaunch a new session bus merely to look for an old app.
        if not os.environ.get("DBUS_SESSION_BUS_ADDRESS") and not os.environ.get("XDG_RUNTIME_DIR"):
            raise NoSession("Пользовательский сеанс не найден; работаю без завершения приложения.")
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as exc:
            raise NoSession("Пользовательская шина недоступна; работаю без завершения приложения.") from exc

    def _call(self, method, name):
        try:
            result = self.connection.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                method, self.GLib.Variant("(s)", (name,)), None,
                self.Gio.DBusCallFlags.NO_AUTO_START, 700, None)
            return result.unpack()[0]
        except self.GLib.Error as exc:
            remote = self.Gio.DBusError.get_remote_error(exc) or ""
            if remote in {"org.freedesktop.DBus.Error.NameHasNoOwner", "org.freedesktop.DBus.Error.ServiceUnknown"}:
                return None
            raise StopError("Не удалось проверить запущенный VELIQEN Link. Закрой его через меню приложения.") from exc

    def owner(self):
        return self._call("GetNameOwner", SERVICE)

    def uid(self, owner):
        return self._call("GetConnectionUnixUser", owner)

    def pid(self, owner):
        return self._call("GetConnectionUnixProcessID", owner)


class ProcessHandle:
    def __init__(self, pid, descriptor, started):
        self.pid, self.descriptor, self.started = pid, descriptor, started


class Processes:
    @staticmethod
    def _identity(pid):
        path = Path("/proc") / str(pid)
        try:
            owner = path.stat().st_uid
            raw_stat = (path / "stat").read_text()
            # comm may contain spaces/parentheses. Fields after its final ')'
            # start at field 3; process starttime is field 22.
            fields = raw_stat[raw_stat.rfind(")") + 2:].split()
            started = int(fields[19])
            command = [os.fsdecode(item) for item in (path / "cmdline").read_bytes().split(b"\0") if item]
            effective_uids = next(line.split()[1:] for line in (path / "status").read_text().splitlines() if line.startswith("Uid:"))
            if len(effective_uids) != 4:
                raise ValueError("invalid uid record")
            return owner, tuple(map(int, effective_uids)), started, command
        except FileNotFoundError:
            return None
        except (OSError, ValueError, IndexError, StopIteration) as exc:
            raise StopError("Не удалось проверить процесс приложения. Заверши VELIQEN Link вручную.") from exc

    @staticmethod
    def _is_link_command(command):
        if not command:
            return False
        executable = Path(command[0]).name
        if executable == "veliqen-link":
            return True
        if not executable.startswith("python"):
            return False
        if any(command[index:index + 2] == ["-m", "veliqen_link"] for index in range(1, len(command))):
            return True
        return len(command) > 1 and Path(command[1]).name == "veliqen-link"

    def open(self, pid, uid):
        if type(pid) is not int or pid <= 1 or pid in {os.getpid(), os.getppid()}:
            raise StopError("Получен неподходящий процесс приложения. Закрой VELIQEN Link вручную.")
        before = self._identity(pid)
        if before is None:
            return None
        owner, uids, started, command = before
        if owner != uid or any(value != uid for value in uids) or not self._is_link_command(command):
            raise StopError("Процесс не подтверждён как твой VELIQEN Link. Автоматическое завершение отменено.")
        if not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise StopError("Безопасное завершение недоступно в этой системе. Закрой VELIQEN Link вручную.")
        try:
            descriptor = os.pidfd_open(pid, 0)
        except ProcessLookupError:
            return None
        except OSError as exc:
            raise StopError("Не удалось безопасно открыть процесс. Закрой VELIQEN Link вручную.") from exc
        try:
            after = self._identity(pid)
        except Exception:
            os.close(descriptor)
            raise
        if after is None:
            os.close(descriptor)
            return None
        if after != before:
            os.close(descriptor)
            raise StopError("Процесс изменился во время проверки. Повтори обновление после закрытия VELIQEN Link.")
        return ProcessHandle(pid, descriptor, started)

    @staticmethod
    def terminate(handle):
        try:
            signal.pidfd_send_signal(handle.descriptor, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            raise StopError("Не удалось завершить VELIQEN Link. Закрой его через меню приложения.") from exc

    @staticmethod
    def wait_exit(handle, timeout):
        readable, _, _ = select.select([handle.descriptor], [], [], max(0.0, min(float(timeout), 5.0)))
        return bool(readable)

    @staticmethod
    def close(handle):
        os.close(handle.descriptor)


def stop_running(bus, processes=None, uid=None, wait_seconds=5.0, clock=time.monotonic):
    """Injectable decision path; a successful result means no named app remains."""
    process = processes or Processes()
    expected_uid = os.getuid() if uid is None else uid
    owner = bus.owner()
    if owner is None:
        return {"state": "absent", "message": "Предыдущий VELIQEN Link не запущен."}
    if not isinstance(owner, str) or not owner.startswith(":"):
        raise StopError("Не удалось установить владельца приложения. Закрой его вручную.")
    actual_uid = bus.uid(owner)
    if actual_uid is None and bus.owner() is None:
        return {"state": "absent", "message": "Предыдущий VELIQEN Link уже закрыт."}
    if actual_uid != expected_uid:
        raise StopError("Приложение принадлежит другому пользователю. Автоматическое завершение отменено.")
    pid = bus.pid(owner)
    if pid is None:
        if bus.owner() is None:
            return {"state": "absent", "message": "Предыдущий VELIQEN Link уже закрыт."}
        raise StopError("Владелец приложения изменился. Закрой VELIQEN Link и повтори обновление.")
    handle = process.open(pid, expected_uid)
    if handle is None:
        if bus.owner() is None:
            return {"state": "absent", "message": "Предыдущий VELIQEN Link уже закрыт."}
        raise StopError("Не удалось подтвердить завершение приложения. Закрой VELIQEN Link вручную.")
    try:
        # Revalidate the unique bus owner after opening a race-safe pidfd.
        current = bus.owner()
        if current is not None and (current != owner or bus.uid(owner) != expected_uid or bus.pid(owner) != pid):
            raise StopError("Запущенное приложение изменилось во время проверки. Повтори обновление после его закрытия.")
        deadline = clock() + max(0.0, min(float(wait_seconds), 5.0))
        if current == owner:
            process.terminate(handle)
        if not process.wait_exit(handle, max(0.0, deadline - clock())):
            raise StopError("VELIQEN Link не завершился за 5 секунд. Закрой его через «Настройки → Завершить приложение» и повтори обновление. Принудительное завершение не выполнялось.")
        if bus.owner() is not None:
            raise StopError("VELIQEN Link снова запущен. Закрой его и повтори обновление.")
        return {"state": "stopped", "message": "Предыдущий VELIQEN Link корректно закрыт. Сопряжения и настройки сохранены."}
    finally:
        process.close(handle)


def main(arguments=None):
    args = sys.argv[1:] if arguments is None else arguments
    if args:
        if args == ["--help"]:
            print("Закрывает только VELIQEN Link текущего пользователя перед обновлением. Не применяет SIGKILL.")
            return 0
        print("Использование: /usr/bin/python3 scripts/STOP-RUNNING.py", file=sys.stderr)
        return 2
    try:
        bus = SessionBus()
    except NoSession as exc:
        print(str(exc))
        return 0
    try:
        result = stop_running(bus)
        print(result["message"])
        return 0
    except (StopError, OSError) as exc:
        print("Обновление остановлено: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
