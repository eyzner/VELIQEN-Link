"""Consent-based Android screen sharing over mutually authenticated TLS.

Uses the already-paired KDE Connect certificates. No ADB, access token, CA
download, password or separate network pairing is involved. The receiving
application must explicitly select allowed device IDs. No frames are recorded.

Wire protocol: unsigned big-endian 32-bit length, one-byte type (0 JSON, 1
JPEG), then body. JSON is <=16 KiB, JPEG <=2 MiB. Android sends hello, frames
and heartbeat; desktop sends ready/alive or a bounded input command.
SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import struct
import threading
import time
import uuid

PORT = 38475
SCREEN_REQUEST = "VELIQEN_LINK_SCREEN_REQUEST_V1"
MAX_JSON = 16 * 1024
MAX_FRAME = 2 * 1024 * 1024


class ScreenLinkError(RuntimeError):
    pass


def _qstring(value):
    value = value.strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1]
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    return re.sub(r'\\([nrt"\\])', lambda m: escapes[m.group(1)], value)


def trusted_certificates(config_dir):
    """Read, but never change, KDE's authoritative trust store."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read(Path(config_dir) / "trusted_devices", encoding="utf-8")
    except (OSError, configparser.Error):
        return {}
    result = {}
    for device_id in parser.sections():
        pem = _qstring(parser.get(device_id, "certificate", fallback=""))
        if not pem.startswith("-----BEGIN CERTIFICATE-----"):
            continue
        try:
            der = ssl.PEM_cert_to_DER_cert(pem)
        except (ValueError, ssl.SSLError):
            continue
        if 100 <= len(der) <= 16384:
            result[device_id] = (pem, hashlib.sha256(der).digest())
    return result


def _read_exact(connection, count):
    data = bytearray()
    while len(data) < count:
        chunk = connection.recv(count - len(data))
        if not chunk:
            raise EOFError("Соединение закрыто телефоном")
        data.extend(chunk)
    return bytes(data)


def read_packet(connection):
    length = struct.unpack("!I", _read_exact(connection, 4))[0]
    if length < 2 or length > MAX_FRAME + 1:
        raise ScreenLinkError("Недопустимый размер кадра")
    kind = _read_exact(connection, 1)[0]
    if kind not in (0, 1) or (kind == 0 and length > MAX_JSON + 1):
        raise ScreenLinkError("Недопустимый пакет экрана")
    body = _read_exact(connection, length - 1)
    if kind == 0:
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise ScreenLinkError("Повреждённый служебный пакет") from exc
        if not isinstance(value, dict):
            raise ScreenLinkError("Ожидался объект протокола экрана")
        return kind, value
    if not body.startswith(b"\xff\xd8") or not body.endswith(b"\xff\xd9"):
        raise ScreenLinkError("Некорректный кадр JPEG")
    return kind, body


def encode_json(value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(data) > MAX_JSON:
        raise ScreenLinkError("Команда слишком длинная")
    return struct.pack("!I", len(data) + 1) + b"\x00" + data


def validate_command(command):
    """Keep remote input deliberately small; no shell, URLs or arbitrary actions."""
    if not isinstance(command, dict):
        raise ScreenLinkError("Некорректная команда")
    op = command.get("op")
    if op in {"back", "home", "recents", "stop"}:
        return {"op": op}
    if op == "text":
        value = command.get("text")
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise ScreenLinkError("Ввод текста ограничен 1024 символами")
        return {"op": op, "text": value}
    if op in {"tap", "swipe"}:
        fields = ("x", "y") if op == "tap" else ("x", "y", "x2", "y2")
        result = {"op": op}
        for name in fields:
            value = command.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ScreenLinkError("Координаты вне экрана")
            result[name] = value
        if op == "swipe":
            duration = command.get("duration", 300)
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration):
                raise ScreenLinkError("Некорректная длительность жеста")
            result["duration"] = max(80, min(1000, int(duration)))
        return result
    raise ScreenLinkError("Неизвестная команда управления")


class ScreenReceiver:
    def __init__(self, on_session=None, on_frame=None, on_end=None, on_status=None,
                 config_dir=None, host="", port=PORT):
        self.config_dir = Path(config_dir or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "kdeconnect")
        self.on_session = on_session or (lambda value: None)
        self.on_frame = on_frame or (lambda value, session_id: None)
        self.on_end = on_end or (lambda value, session_id: None)
        self.on_status = on_status or (lambda value: None)
        self.host, self.port = host, port
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._allowed = set()
        self._listener = None
        self._connection = None
        self._session = None
        self._session_digest = None
        self._closing = threading.Event()
        self._slots = threading.BoundedSemaphore(2)
        self._status = "Открой VELIQEN Link на телефоне"

    def _set_status(self, text):
        with self._lock:
            self._status = text
        self.on_status(text)

    def set_allowed_devices(self, device_ids):
        allowed = {item for item in device_ids if isinstance(item, str) and item}
        with self._lock:
            self._allowed = allowed
            revoke = self._session["session_id"] if self._session is not None and self._session["device_id"] not in allowed else None
        if revoke is not None:
            self.stop_session(session_id=revoke)

    def start(self):
        with self._lock:
            if self._listener is not None:
                return self.snapshot()
            if not (self.config_dir / "certificate.pem").is_file() or not (self.config_dir / "privateKey.pem").is_file():
                self._set_status("Сначала подключи телефон к VELIQEN Link")
                return self.snapshot()
            self._closing.clear()
            try:
                if self.host:
                    listener = socket.create_server((self.host, self.port), family=socket.AF_INET)
                elif socket.has_dualstack_ipv6():
                    listener = socket.create_server(("", self.port), family=socket.AF_INET6, dualstack_ipv6=True)
                else:
                    listener = socket.create_server(("", self.port), family=socket.AF_INET)
                listener.settimeout(0.5)
            except OSError as exc:
                self._set_status("Не удалось подготовить приём экрана. Возможно, VELIQEN Link уже запущен.")
                raise ScreenLinkError(self._status) from exc
            self._listener = listener
            self.port = listener.getsockname()[1]
            self._set_status("Готово: нажми «Показать экран» на телефоне")
            threading.Thread(target=self._accept, name="veliqen-screen-listener", daemon=True).start()
        return self.snapshot()

    def _accept(self):
        listener = self._listener
        while not self._closing.is_set():
            try:
                connection, _address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            if not self._slots.acquire(blocking=False):
                connection.close()
                continue
            threading.Thread(target=self._receive, args=(connection,), name="veliqen-screen-session", daemon=True).start()

    def _context(self, certificates):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(self.config_dir / "certificate.pem", self.config_dir / "privateKey.pem")
        context.load_verify_locations(cadata="\n".join(item[0] for item in certificates.values()))
        # Existing KDE self-signed identities are pinned leaves, not a public CA.
        context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        return context

    def _still_trusted(self, device_id, digest):
        with self._lock:
            if device_id not in self._allowed or self._closing.is_set():
                return False
        current = trusted_certificates(self.config_dir).get(device_id)
        return current is not None and current[1] == digest

    def _receive(self, connection):
        owned = False
        session_id = None
        reason = "Показ экрана завершён на телефоне"
        try:
            connection.settimeout(8)
            certificates = trusted_certificates(self.config_dir)
            with self._lock:
                certificates = {key: value for key, value in certificates.items() if key in self._allowed}
                if self._connection is not None:
                    return
            if not certificates:
                return
            connection = self._context(certificates).wrap_socket(connection, server_side=True)
            digest = hashlib.sha256(connection.getpeercert(binary_form=True)).digest()
            matched = [key for key, value in certificates.items() if value[1] == digest]
            if len(matched) != 1:
                raise ScreenLinkError("Телефон не подтверждён")
            device_id = matched[0]
            kind, hello = read_packet(connection)
            if kind != 0 or hello.get("type") != "hello" or hello.get("protocol") != 1 or hello.get("deviceId") != device_id:
                raise ScreenLinkError("Несовместимая версия показа экрана")
            if not self._still_trusted(device_id, digest):
                return
            session_id = uuid.uuid4().hex
            session = {"session_id": session_id, "device_id": device_id, "name": str(hello.get("name", "Телефон"))[:100],
                       "control": hello.get("control") is True}
            with self._lock:
                if self._connection is not None or self._closing.is_set() or device_id not in self._allowed:
                    return
                self._connection, self._session = connection, session
                self._session_digest = digest
                self._status = "Экран телефона открыт"
                owned = True
            connection.settimeout(12)
            with self._write_lock:
                connection.sendall(encode_json({"op": "ready", "protocol": 1}))
            self.on_session(dict(session))
            last_alive = time.monotonic()
            while not self._closing.is_set():
                kind, value = read_packet(connection)
                if not self._still_trusted(device_id, digest):
                    reason = "Доступ телефона к показу экрана отключён"
                    break
                if kind == 1:
                    self.on_frame(value, session_id)
                elif value.get("type") == "heartbeat":
                    pass
                else:
                    raise ScreenLinkError("Неподдерживаемый пакет экрана")
                if time.monotonic() - last_alive >= 2:
                    with self._write_lock:
                        connection.sendall(encode_json({"op": "alive"}))
                    last_alive = time.monotonic()
        except (OSError, ssl.SSLError, EOFError, ScreenLinkError, ValueError):
            if owned and not self._closing.is_set():
                reason = "Связь экрана завершена. Повторно нажми «Показать экран» на телефоне."
        finally:
            try:
                connection.close()
            except OSError:
                pass
            with self._lock:
                current = owned and self._connection is connection
                if current:
                    self._connection = self._session = None
                    self._session_digest = None
                    self._status = reason
            if current:
                self.on_end(reason, session_id)
            self._slots.release()

    def send_command(self, command, session_id=None):
        clean = validate_command(command)
        with self._lock:
            connection, session, digest = self._connection, self._session, self._session_digest
        if connection is None or session is None:
            raise ScreenLinkError("Экран сейчас не передаётся")
        if session_id is not None and session["session_id"] != session_id:
            raise ScreenLinkError("Этот сеанс экрана уже завершён")
        if clean["op"] != "stop" and not session["control"]:
            raise ScreenLinkError("Управление не разрешено на телефоне; доступен просмотр")
        if not self._still_trusted(session["device_id"], digest):
            self.stop_session(session_id=session["session_id"])
            raise ScreenLinkError("Сопряжение телефона отменено")
        with self._write_lock:
            try:
                connection.sendall(encode_json(clean))
            except OSError as exc:
                raise ScreenLinkError("Соединение экрана закрыто") from exc
        return True

    def stop_session(self, session_id=None):
        with self._lock:
            connection = self._connection
            if session_id is not None and (self._session is None or self._session["session_id"] != session_id):
                return
        if connection is not None:
            # EOF is the stop signal too. Do not block the GTK caller on a write
            # to a phone which lost Wi-Fi or no longer reads input.
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()

    def snapshot(self):
        with self._lock:
            return {"listening": self._listener is not None, "port": self.port,
                    "status": self._status, "session": dict(self._session) if self._session else None}

    def close(self):
        self._closing.set()
        self.stop_session()
        with self._lock:
            listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
