"""Authorized ADB pairing and scrcpy sessions, without a shell or TCP-IP reset.

Pairing codes are passed through stdin, never command arguments, files or logs.
The module does not call `adb tcpip`, root, reverse, shell or install.
"""
from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading


class ScreenError(RuntimeError):
    pass


def validate_endpoint(endpoint):
    endpoint = str(endpoint).strip()
    if endpoint.startswith("["):
        match = re.fullmatch(r"\[([0-9A-Fa-f:]+)\]:(\d{1,5})", endpoint)
        if not match:
            raise ScreenError("Введите адрес в формате IP:порт или [IPv6]:порт.")
        try:
            ipaddress.IPv6Address(match.group(1))
        except ValueError as exc:
            raise ScreenError("Неверный IPv6-адрес.") from exc
        port = int(match.group(2))
    else:
        match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9.\-]{0,252}):(\d{1,5})", endpoint)
        if not match or ".." in match.group(1):
            raise ScreenError("Введите IP-адрес телефона и порт, например 192.168.1.25:37145.")
        host, port = match.group(1), int(match.group(2))
        if re.fullmatch(r"[0-9.]+", host):
            try:
                ipaddress.IPv4Address(host)
            except ValueError as exc:
                raise ScreenError("Неверный IPv4-адрес.") from exc
        elif any(not label or label.startswith("-") or label.endswith("-") or len(label) > 63 for label in host.split(".")):
            raise ScreenError("Неверное имя телефона.")
    if not 1 <= port <= 65535:
        raise ScreenError("Порт должен быть от 1 до 65535.")
    return endpoint


def parse_devices(text):
    devices = []
    for line in text.splitlines():
        if not line.strip() or line.startswith(("List of devices", "*", "adb ")):
            continue
        fields = line.split()
        if len(fields) < 2 or fields[1] not in ("device", "unauthorized", "offline", "recovery", "sideload", "bootloader", "no"):
            continue
        attrs = dict(f.split(":", 1) for f in fields[2:] if ":" in f)
        devices.append({"serial": fields[0], "state": fields[1],
                        "model": attrs.get("model", fields[0]).replace("_", " "),
                        "transport_id": attrs.get("transport_id", ""),
                        "authorized": fields[1] == "device"})
    return devices


class ScreenManager:
    def __init__(self, runner=None, popen=None, which=None, bundled_dirs=None):
        self._run = runner or subprocess.run
        self._popen = popen or subprocess.Popen
        self._system_which = which or shutil.which
        self._bundled_dirs = tuple(Path(p) for p in bundled_dirs) if bundled_dirs is not None else (
            Path("/usr/lib/veliqen-link/scrcpy"),
            Path.home() / ".local/lib/veliqen-link/scrcpy",
            Path("/opt/veliqen-link/vendor/scrcpy"),
            Path(__file__).resolve().parents[2] / "vendor/scrcpy",
        )
        self._which = self._find_tool
        self._process = None
        self._lock = threading.RLock()
        self._capabilities = None
        self.last_error = ""
        self.last_exit_code = None
        self.serial = ""

    def _find_tool(self, name):
        # Only known VELIQEN Link installation locations take priority. The
        # global system PATH and environment are not modified.
        for directory in self._bundled_dirs:
            candidate = directory / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        return self._system_which(name)

    def _environment(self, executable):
        environment = os.environ.copy()
        parent = Path(executable).parent
        if parent in self._bundled_dirs:
            environment["PATH"] = str(parent) + os.pathsep + environment.get("PATH", "")
            server = parent / "scrcpy-server"
            if server.is_file():
                environment["SCRCPY_SERVER_PATH"] = str(server)
        adb = self._which("adb")
        if adb:
            environment["ADB"] = adb
        return environment

    def _tool(self, name):
        path = self._which(name)
        if not path:
            raise ScreenError(f"Не найдена программа {name}. Переустанови пакет VELIQEN Link через установщик приложений Ubuntu.")
        return path

    def _command(self, args, *, timeout=15, input_text=None, sensitive=False):
        try:
            completed = self._run(args, input=input_text, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, timeout=timeout, check=False,
                                  env=self._environment(args[0]))
        except subprocess.TimeoutExpired as exc:
            raise ScreenError("Телефон не ответил вовремя. Проверьте Wi-Fi, адрес, порт и экран телефона.") from exc
        except (OSError, ValueError) as exc:
            raise ScreenError("Не удалось запустить ADB/scrcpy. Проверьте установку программ.") from exc
        output = (completed.stdout or "") + "\n" + (completed.stderr or "")
        # Never surface a pairing transcript: some adb builds echo stdin.
        if completed.returncode != 0:
            if sensitive:
                raise ScreenError("Сопряжение не выполнено. Откройте на телефоне новое окно с кодом и проверьте именно порт сопряжения.")
            detail = output.strip()[-600:]
            raise ScreenError(detail or "ADB/scrcpy завершился с ошибкой.")
        return output.strip()

    def available(self):
        missing = [name for name in ("adb", "scrcpy") if not self._which(name)]
        return {"available": not missing, "adb": bool(self._which("adb")),
                "scrcpy": bool(self._which("scrcpy")), "missing": missing,
                "status": "Экран доступен после разрешения отладки на телефоне." if not missing
                else "Не установлено: " + ", ".join(missing)}

    def devices(self):
        return parse_devices(self._command([self._tool("adb"), "devices", "-l"]))

    def pair(self, endpoint, code):
        endpoint = validate_endpoint(endpoint)
        code = str(code).strip()
        if not re.fullmatch(r"\d{6}", code):
            raise ScreenError("Введите шестизначный код из окна беспроводного сопряжения телефона.")
        # Deliberately omit the optional password CLI argument, which would be
        # visible to process-list tools.  Never include code in any exception.
        output = self._command([self._tool("adb"), "pair", endpoint],
                               timeout=25, input_text=code + "\n", sensitive=True)
        if "successfully paired" not in output.lower():
            raise ScreenError("ADB не подтвердил сопряжение. Создайте новый код и повторите попытку.")
        return "Сопряжение выполнено. Теперь укажите адрес и порт подключения с главного экрана беспроводной отладки."

    def connect(self, endpoint):
        endpoint = validate_endpoint(endpoint)
        output = self._command([self._tool("adb"), "connect", endpoint], timeout=20)
        if "connected to" not in output.lower() or any(s in output.lower() for s in ("failed", "cannot", "unable")):
            raise ScreenError(output[-500:] or "ADB не подтвердил подключение.")
        return "Подключение создано. Обновите устройства и выберите телефон со статусом device."

    def disconnect(self, endpoint):
        # Explicit single endpoint only.  Never disconnect every ADB device.
        endpoint = validate_endpoint(endpoint)
        self._command([self._tool("adb"), "disconnect", endpoint])
        return "Соединение с выбранным адресом отключено."

    def _options(self):
        if self._capabilities is None:
            self._capabilities = self._command([self._tool("scrcpy"), "--help"], timeout=8)
        return self._capabilities

    def launch(self, serial, screen_off=False, max_size=1600, on_exit=None):
        """Start one selected authorized device; on_exit(code, message) is optional.

        on_exit runs on a background thread. A GTK caller must dispatch with
        GLib.idle_add. Android media audio is disabled when supported so that it
        does not conflict with the separately selected Bluetooth call route.
        """
        serial = str(serial).strip()
        if not re.fullmatch(r"[A-Za-z0-9._:\-\[\]%]{1,256}", serial) or serial.startswith("-"):
            raise ScreenError("Выберите телефон из списка устройств.")
        if not isinstance(max_size, int) or not 320 <= max_size <= 4096:
            raise ScreenError("Размер экрана должен быть от 320 до 4096 пикселей.")
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise ScreenError("Окно телефона уже открыто. Закройте его перед новым запуском.")
            device = next((d for d in self.devices() if d["serial"] == serial), None)
            if not device:
                raise ScreenError("Телефон отсутствует в ADB. Подключите его и обновите список.")
            if device["state"] != "device":
                raise ScreenError("Телефон не разрешил подключение. Разблокируйте его и подтвердите ключ этого компьютера.")
            args = [self._tool("scrcpy"), "--serial=" + serial,
                    "--window-title=VELIQEN Link — телефон", "--max-size=" + str(max_size)]
            options = self._options()
            if "--no-audio" in options:
                args.append("--no-audio")
            if screen_off:
                if "--turn-screen-off" not in options:
                    raise ScreenError("Установленная версия scrcpy не поддерживает выключение экрана телефона.")
                args.append("--turn-screen-off")
            try:
                process = self._popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                      stderr=subprocess.STDOUT, text=True, bufsize=1,
                                      start_new_session=True, env=self._environment(args[0]))
            except OSError as exc:
                raise ScreenError("Не удалось открыть scrcpy.") from exc
            self._process = process
            self.serial = serial
            self.last_error = ""
            self.last_exit_code = None
            threading.Thread(target=self._collect, args=(process, on_exit), daemon=True).start()
            return process.pid

    def _collect(self, process, on_exit):
        tail = ""
        try:
            if process.stdout is not None:
                # Bounded memory even if scrcpy stays open for days.
                for line in process.stdout:
                    tail = (tail + line)[-4000:]
            code = process.wait()
        except Exception:
            code = -1
            tail = "Не удалось получить состояние окна телефона."
        message = tail[-1000:] if code else "Окно телефона закрыто."
        with self._lock:
            if self._process is process:
                self.last_exit_code = code
                self.last_error = message if code else ""
        if on_exit is not None:
            try:
                on_exit(code, message)
            except Exception:
                pass

    def status(self):
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {"running": running, "serial": self.serial,
                    "pid": self._process.pid if running else None,
                    "exit_code": self.last_exit_code, "error": self.last_error}

    def stop(self):
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            except ProcessLookupError:
                pass  # The user may have closed the window after poll().
        return "Окно телефона закрыто. Соединение ADB можно отдельно отключить в настройках телефона."

    close = stop
