"""KDE Connect session-bus adapter, independent of the GUI toolkit.

All public network methods are synchronous and belong on a worker thread.
Each D-Bus request has a finite timeout. SMS/history requests additionally use
a private GLib context for a short, bounded signal-collection window.

Protocol reference: KDE/kdeconnect-kde @ 6fa6dac15f1019bff6a922cf3014e222f5140f66.
See ../KDE_BACKEND.md for the source files and exact wire signatures.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib


SERVICE = "org.kde.kdeconnect"
ROOT = "/modules/kdeconnect"
DEVICE_INTERFACE = SERVICE + ".device"
DAEMON_INTERFACE = SERVICE + ".daemon"
CONVERSATIONS_INTERFACE = DEVICE_INTERFACE + ".conversations"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
INTROSPECT_INTERFACE = "org.freedesktop.DBus.Introspectable"
MESSAGE_SIGNATURE = "(isa(s)xiixixa(xsss))"


class KdeError(RuntimeError):
    """An actionable error, without message bodies or other sensitive values."""

    def __init__(self, message: str, code: str = "kde_error", *, uncertain=False):
        super().__init__(message)
        self.code = code
        self.uncertain = uncertain


class ApiUnavailable(KdeError):
    def __init__(self, message: str):
        super().__init__(message, "unsupported_api")


def _unpack(value):
    if isinstance(value, GLib.Variant):
        return _unpack(value.unpack())
    if isinstance(value, dict):
        return {key: _unpack(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(_unpack(item) for item in value)
    return value


def decode_message(value) -> dict:
    """Decode KDE's registered ConversationMessage D-Bus STRUCT, not JSON.

    Also accepts a field map, for distributions that expose this documented
    alternative. Unknown structures fail explicitly instead of mislabelling
    a recipient or a SIM subscription.
    """
    data = _unpack(value)
    if isinstance(data, (list, tuple)) and len(data) == 10:
        event, body, addresses, date, kind, read, thread, uid, sim, attachments = data
    elif isinstance(data, dict):
        event = data.get("event", 1)
        body = data.get("body", "")
        addresses = data.get("addresses", [])
        date = data.get("date", 0)
        kind = data.get("type", 0)
        read = data.get("read", 0)
        thread = data.get("thread_id", data.get("threadID"))
        uid = data.get("_id", data.get("uID", 0))
        sim = data.get("sub_id", data.get("subID", -1))
        attachments = data.get("attachments", [])
    else:
        raise ApiUnavailable("Формат SMS этой версии KDE Connect не поддерживается. Откройте штатное окно SMS.")
    numbers = []
    for address in addresses:
        address = _unpack(address)
        if isinstance(address, (tuple, list)) and len(address) == 1:
            number = address[0]
        elif isinstance(address, dict):
            number = address.get("address", "")
        elif isinstance(address, str):
            number = address
        else:
            raise ApiUnavailable("KDE Connect вернул неизвестный формат адреса SMS.")
        if number:
            numbers.append(str(number))
    files = []
    for attachment in attachments:
        attachment = _unpack(attachment)
        if isinstance(attachment, (tuple, list)) and len(attachment) == 4:
            part_id, mime, _thumbnail, identifier = attachment
            files.append({"part_id": int(part_id), "mime": str(mime), "identifier": str(identifier)})
        elif isinstance(attachment, dict):
            files.append({"part_id": attachment.get("part_id"),
                          "mime": attachment.get("mime_type", ""),
                          "identifier": attachment.get("unique_identifier", "")})
    try:
        return {"id": int(uid), "thread_id": int(thread), "body": str(body),
                "addresses": numbers, "date": int(date), "type": int(kind),
                "read": bool(read), "sim": int(sim), "incoming": int(kind) == 1,
                "event": int(event), "attachments": files}
    except (TypeError, ValueError, OverflowError) as error:
        raise ApiUnavailable("KDE Connect вернул неполные данные SMS.") from error


class KdeBackend:
    def __init__(self, connection=None, timeout_ms=4000, signal_wait_ms=1600):
        self._connection = connection
        self.timeout_ms = max(100, min(int(timeout_ms), 15000))
        self.signal_wait_ms = max(100, min(int(signal_wait_ms), 5000))
        self._xml_cache = {}
        self._lock = threading.RLock()
        self._version = None

    @property
    def connection(self):
        with self._lock:
            if self._connection is None:
                try:
                    self._connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
                except GLib.Error as error:
                    raise KdeError("Не удалось подключиться к сеансу Ubuntu. Запускайте приложение от обычного пользователя, без sudo.", "no_session_bus") from error
            return self._connection

    def _raw_call(self, path, interface, method, parameters=None, *, mutating=False):
        try:
            result = self.connection.call_sync(
                SERVICE, path, interface, method, parameters, None,
                Gio.DBusCallFlags.NONE, self.timeout_ms, None)
            return _unpack(result)
        except GLib.Error as error:
            with self._lock:
                self._xml_cache.pop(path, None)
            name = Gio.DBusError.get_remote_error(error) or ""
            if "ServiceUnknown" in name or "NameHasNoOwner" in name or "Spawn" in name:
                message = "Служба KDE Connect недоступна. Установите пакет kdeconnect и откройте его настройки."
                code = "service_unavailable"
            elif any(token in name for token in ("UnknownMethod", "UnknownInterface", "UnknownObject")):
                message = "Функция недоступна: устройство отключилось, плагин выключен или версия KDE Connect отличается."
                code = "unsupported_api"
            elif any(token in name for token in ("AccessDenied", "AuthFailed")):
                message = "KDE Connect отказал в доступе. Проверьте сопряжение и разрешения на телефоне."
                code = "access_denied"
            else:
                message = "KDE Connect не подтвердил выполнение запроса. Проверьте соединение с телефоном."
                code = "request_failed"
            if mutating and code == "request_failed":
                message += " Перед повторной отправкой SMS или файла проверьте результат на телефоне."
            raise KdeError(message, code, uncertain=mutating and code == "request_failed") from error

    def _interfaces(self, path):
        with self._lock:
            cached = self._xml_cache.get(path)
        if cached and time.monotonic() - cached[0] < 3:
            return cached[1]
        xml = self._raw_call(path, INTROSPECT_INTERFACE, "Introspect")[0]
        if not isinstance(xml, str) or len(xml) > 1_000_000:
            raise ApiUnavailable("KDE Connect вернул некорректное описание интерфейса.")
        try:
            node = ET.fromstring(xml)
        except ET.ParseError as error:
            raise ApiUnavailable("Не удалось прочитать интерфейс KDE Connect.") from error
        interfaces = {}
        for interface in node.findall("interface"):
            methods = {}
            for method in interface.findall("method"):
                signature = "".join(arg.get("type", "") for arg in method.findall("arg")
                                    if arg.get("direction", "in") == "in")
                methods.setdefault(method.get("name"), []).append(signature)
            interfaces[interface.get("name")] = methods
        with self._lock:
            self._xml_cache[path] = (time.monotonic(), interfaces)
        return interfaces

    def _has_method(self, path, interface, method, signature):
        return signature in self._interfaces(path).get(interface, {}).get(method, [])

    def _call(self, path, interface, method, signature="", args=(), *, mutating=False):
        if not self._has_method(path, interface, method, signature):
            raise ApiUnavailable(
                "Эта функция не предоставляется установленной версией KDE Connect. "
                "Проверьте плагины в настройках или откройте штатное окно KDE Connect.")
        parameters = GLib.Variant("(" + signature + ")", tuple(args)) if signature else None
        return self._raw_call(path, interface, method, parameters, mutating=mutating)

    @staticmethod
    def _path(device_id):
        device_id = str(device_id)
        if not re.fullmatch(r"[A-Za-z0-9_]+", device_id):
            raise KdeError("Некорректный идентификатор устройства.", "invalid_device")
        return ROOT + "/devices/" + device_id

    def _properties(self, path, interface):
        result = self._raw_call(path, PROPERTIES_INTERFACE, "GetAll", GLib.Variant("(s)", (interface,)))
        return result[0] if result else {}

    def device(self, device_id):
        props = self._properties(self._path(device_id), DEVICE_INTERFACE)
        if "isPaired" not in props:
            raise ApiUnavailable("Данные устройства недоступны. Обновите список устройств.")
        return {"id": str(device_id), "name": props.get("name", str(device_id)),
                "type": props.get("type", "unknown"),
                "paired": bool(props.get("isPaired", False)),
                "reachable": bool(props.get("isReachable", False)),
                "pair_requested": bool(props.get("isPairRequested", False)),
                "pair_requested_by_peer": bool(props.get("isPairRequestedByPeer", False)),
                "verification_key": props.get("verificationKey", ""),
                "addresses": props.get("reachableAddresses", []),
                "providers": props.get("activeProviderNames", []),
                "connection": props.get("connectionDescription", ""),
                "plugins": props.get("supportedPlugins", [])}

    def _ready(self, device_id):
        device = self.device(device_id)
        if not device["paired"]:
            raise KdeError("Сначала выполните сопряжение и подтвердите его на телефоне.", "not_paired")
        if not device["reachable"]:
            raise KdeError("Телефон сейчас недоступен. Проверьте Wi-Fi и откройте VELIQEN Link на телефоне.", "offline")
        return self._path(device_id)

    def devices(self):
        if self._has_method(ROOT, DAEMON_INTERFACE, "devices", "bb"):
            ids = self._call(ROOT, DAEMON_INTERFACE, "devices", "bb", (False, False))[0]
        else:
            ids = self._call(ROOT, DAEMON_INTERFACE, "devices")[0]
        devices = []
        for device_id in ids:
            try:
                devices.append(self.device(device_id))
            except KdeError:
                # Discovery may remove an unpaired device between these calls.
                continue
        return sorted(devices, key=lambda item: (not item["reachable"], not item["paired"], item["name"].casefold()))

    def status(self):
        """Read availability without implicitly starting a desktop service."""
        installed = shutil.which("kdeconnect-cli") is not None
        running = False
        problem = ""
        try:
            reply = self.connection.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "NameHasOwner", GLib.Variant("(s)", (SERVICE,)),
                GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, self.timeout_ms, None)
            running = bool(reply.unpack()[0])
        except (GLib.Error, KdeError):
            problem = "Сеанс D-Bus недоступен. Запустите приложение от обычного пользователя."
        if installed and self._version is None:
            try:
                result = subprocess.run([shutil.which("kdeconnect-cli"), "--version"],
                                        capture_output=True, text=True, timeout=3, check=False)
                self._version = result.stdout.strip()[:160] if result.returncode == 0 else ""
            except (OSError, subprocess.TimeoutExpired):
                self._version = ""
        return {"available": running, "running": running, "installed": installed,
                "version": self._version or "", "error": problem}

    def start_service(self):
        """Activate the packaged user service; no sudo, shell or system edits."""
        try:
            self.connection.call_sync(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                "StartServiceByName", GLib.Variant("(su)", (SERVICE, 0)),
                GLib.VariantType.new("(u)"), Gio.DBusCallFlags.NONE, self.timeout_ms, None)
        except GLib.Error as error:
            raise KdeError("Не удалось запустить KDE Connect. Установите пакет kdeconnect и откройте его настройки.", "service_unavailable") from error
        return self.status()

    def refresh(self):
        with self._lock:
            self._xml_cache.clear()
        self._call(ROOT, DAEMON_INTERFACE, "forceOnNetworkChange")

    def pair(self, device_id):
        device = self.device(device_id)
        if device["paired"]:
            return {"status": "paired"}
        if not device["reachable"]:
            raise KdeError("Устройство недоступно для сопряжения.", "offline")
        method = "acceptPairing" if device["pair_requested_by_peer"] else "requestPairing"
        self._call(self._path(device_id), DEVICE_INTERFACE, method, mutating=True)
        return {"status": "requested", "message": "Проверьте код и подтвердите сопряжение на втором устройстве."}

    def unpair(self, device_id):
        self._call(self._path(device_id), DEVICE_INTERFACE, "unpair", mutating=True)
        with self._lock:
            self._xml_cache.clear()

    def ping(self, device_id, message=None):
        """Use the verified upstream overload; never downgrade a message ping."""
        if message is None:
            self._call(self._ready(device_id) + "/ping", DEVICE_INTERFACE + ".ping", "sendPing", mutating=True)
            return
        if not isinstance(message, str) or not message or "\0" in message or len(message.encode("utf-8")) > 4096:
            raise KdeError("Сообщение для телефона пустое или слишком длинное.", "invalid_message")
        self._call(self._ready(device_id) + "/ping", DEVICE_INTERFACE + ".ping", "sendPing",
                   "s", (message,), mutating=True)

    def ring(self, device_id):
        self._call(self._ready(device_id) + "/findmyphone", DEVICE_INTERFACE + ".findmyphone", "ring", mutating=True)

    def battery(self, device_id):
        props = self._properties(self._ready(device_id) + "/battery", DEVICE_INTERFACE + ".battery")
        if "charge" not in props:
            raise ApiUnavailable("Сведения о батарее пока недоступны.")
        return {"charge": int(props["charge"]), "charging": bool(props.get("isCharging", False)),
                "has_battery": bool(props.get("hasBattery", True))}

    get_battery = battery

    def share_file(self, device_id, path):
        try:
            file = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise KdeError("Выбранный файл не найден.", "invalid_file") from error
        if not file.is_file() or not os.access(file, os.R_OK):
            raise KdeError("Выберите доступный для чтения файл. Для папки сначала создайте архив.", "invalid_file")
        self._call(self._ready(device_id) + "/share", DEVICE_INTERFACE + ".share", "shareUrl", "s", (file.as_uri(),), mutating=True)
        return {"status": "requested", "message": "Передача запрошена; ход выполнения показывает KDE Connect."}

    def share_url(self, device_id, url):
        url = str(url).strip()
        try:
            parsed = urlsplit(url)
        except ValueError as error:
            raise KdeError("Некорректная ссылка.", "invalid_url") from error
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc or any(ord(c) < 32 for c in url):
            raise KdeError("Введите полную ссылку, начинающуюся с https:// или http://.", "invalid_url")
        self._call(self._ready(device_id) + "/share", DEVICE_INTERFACE + ".share", "shareUrl", "s", (url,), mutating=True)

    def send_clipboard(self, device_id, text):
        """Send explicitly supplied text to Android's clipboard.

        Upstream Android SharePlugin.receiveText uses setPrimaryClip. Using
        shareText avoids pretending a background Qt daemon can read GNOME's
        Wayland clipboard. The GUI obtains text from its own clipboard access.
        """
        if not isinstance(text, str) or not text:
            raise KdeError("В буфере нет текста для передачи.", "empty_clipboard")
        self._call(self._ready(device_id) + "/share", DEVICE_INTERFACE + ".share", "shareText", "s", (text,), mutating=True)
        return {"status": "requested"}

    def send_current_clipboard(self, device_id):
        """Use KDE's own clipboard integration where the desktop supports it."""
        self._call(self._ready(device_id) + "/clipboard", DEVICE_INTERFACE + ".clipboard", "sendClipboard", mutating=True)

    def _collect_message_signals(self, device_id, trigger, wait_ms):
        path = self._path(device_id)
        context = GLib.MainContext.new()
        loop = GLib.MainLoop.new(context, False)
        subscriptions = []
        messages = {}
        errors = []
        source = None

        def receive(_connection, _sender, _path, _interface, _signal, parameters, _data):
            try:
                item = decode_message(parameters.get_child_value(0))
                messages[(item["thread_id"], item["id"])] = item
            except KdeError as error:
                errors.append(error)

        context.push_thread_default()
        try:
            for signal in ("conversationCreated", "conversationUpdated"):
                subscriptions.append(self.connection.signal_subscribe(
                    SERVICE, CONVERSATIONS_INTERFACE, signal, path, None,
                    Gio.DBusSignalFlags.NONE, receive, None))
            trigger()
            source = GLib.timeout_source_new(wait_ms)
            source.set_callback(lambda *_args: (loop.quit(), False)[1])
            source.attach(context)
            loop.run()
        finally:
            if source is not None:
                source.destroy()
            for subscription in subscriptions:
                self.connection.signal_unsubscribe(subscription)
            context.pop_thread_default()
        if errors and not messages:
            raise errors[0]
        return list(messages.values())

    def _active_conversations(self, device_id):
        result = self._call(self._path(device_id), CONVERSATIONS_INTERFACE, "activeConversations")
        return [decode_message(item) for item in (result[0] if result else [])]

    def conversations(self, device_id):
        """Latest message from every known SMS thread; refresh once if empty.

        The Android reply is asynchronous. An empty list can mean that the
        phone has not replied yet, not necessarily that its SMS inbox is empty.
        """
        path = self._ready(device_id)
        cached = self._active_conversations(device_id)
        trigger = lambda: self._call(path, CONVERSATIONS_INTERFACE, "requestAllConversationThreads")
        if cached:
            trigger()
            return sorted(cached, key=lambda item: item["date"], reverse=True)
        self._collect_message_signals(device_id, trigger, min(self.signal_wait_ms, 1200))
        return sorted(self._active_conversations(device_id), key=lambda item: item["date"], reverse=True)

    def messages(self, device_id, thread, limit=100):
        path = self._ready(device_id)
        try:
            thread = int(thread)
            limit = max(1, min(int(limit), 200))
            if thread < 0 or thread >= 2 ** 63:
                raise ValueError
        except (ValueError, TypeError) as error:
            raise KdeError("Некорректный диалог SMS.", "invalid_thread") from error
        cached = [item for item in self._active_conversations(device_id) if item["thread_id"] == thread]
        received = self._collect_message_signals(
            device_id,
            lambda: self._call(path, CONVERSATIONS_INTERFACE, "requestConversation", "xii", (thread, 0, limit)),
            self.signal_wait_ms)
        result = {item["id"]: item for item in cached + received if item["thread_id"] == thread}
        if not result:
            raise KdeError("Телефон ещё не передал сообщения этого диалога. Проверьте доступ к SMS на телефоне и обновите список.", "messages_pending")
        return sorted(result.values(), key=lambda item: (item["date"], item["id"]))[-limit:]

    def send_sms(self, device_id, number, text, sim=-1):
        """Queue one SMS request. `sim` is Android subscription ID, not slot 1/2.

        A successful return acknowledges a D-Bus request, not carrier delivery.
        Requests are deliberately never retried automatically.
        """
        number = str(number).strip()
        if not re.fullmatch(r"[+0-9*#(). -]{1,80}", number) or not any(c.isdigit() for c in number):
            raise KdeError("Введите номер получателя SMS.", "invalid_number")
        if not isinstance(text, str) or not text.strip():
            raise KdeError("Введите текст SMS.", "empty_message")
        try:
            sim = int(sim) if sim is not None else -1
            if sim < -1 or sim >= 2 ** 63:
                raise ValueError
        except (ValueError, TypeError) as error:
            raise KdeError("Нужен Android subscription ID SIM-карты или -1 для выбора по умолчанию.", "invalid_sim") from error
        path = self._ready(device_id)
        addresses = [GLib.Variant("(s)", (number,))]
        sms_path = path + "/sms"
        sms_interface = DEVICE_INTERFACE + ".sms"
        if self._has_method(sms_path, sms_interface, "sendSms", "avsavx"):
            self._call(sms_path, sms_interface, "sendSms", "avsavx", (addresses, text, [], sim), mutating=True)
        elif sim == -1:
            # Older releases expose the same address structure without subID.
            self._call(path, CONVERSATIONS_INTERFACE, "sendWithoutConversation", "avsav", (addresses, text, []), mutating=True)
        else:
            raise ApiUnavailable("Эта версия KDE Connect не позволяет выбрать SIM. Используйте SIM по умолчанию или штатное окно SMS.")
        return {"status": "requested", "message": "Запрос отправки передан телефону. Статус доставки проверяйте в SMS на телефоне."}

    def notifications(self, device_id):
        path = self._ready(device_id) + "/notifications"
        interface = DEVICE_INTERFACE + ".notifications"
        ids = self._call(path, interface, "activeNotifications")[0]
        result = []
        for notification_id in ids:
            if not re.fullmatch(r"[A-Za-z0-9_]+", str(notification_id)):
                continue
            try:
                props = self._properties(path + "/" + str(notification_id), interface + ".notification")
            except KdeError:
                continue  # A notification can disappear while the list is read.
            result.append({"id": str(notification_id), "app": props.get("appName", ""),
                           "title": props.get("title", ""), "text": props.get("text", ""),
                           "dismissable": bool(props.get("dismissable", False)),
                           "reply_available": bool(props.get("replyId", ""))})
        return result

    def _notification(self, device_id, notification_id):
        if not re.fullmatch(r"[A-Za-z0-9_]+", str(notification_id)):
            raise KdeError("Некорректный идентификатор уведомления.", "invalid_notification")
        path = self._ready(device_id) + "/notifications/" + str(notification_id)
        interface = DEVICE_INTERFACE + ".notifications.notification"
        return path, interface, self._properties(path, interface)

    def dismiss_notification(self, device_id, notification_id):
        path, interface, props = self._notification(device_id, notification_id)
        if not props.get("dismissable"):
            raise KdeError("Это уведомление нельзя закрыть удалённо.", "not_dismissable")
        self._call(path, interface, "dismiss", mutating=True)

    def reply_notification(self, device_id, notification_id, text):
        if not isinstance(text, str) or not text.strip():
            raise KdeError("Введите текст ответа.", "empty_message")
        path, interface, props = self._notification(device_id, notification_id)
        if not props.get("replyId"):
            raise KdeError("Приложение на телефоне не предоставило быстрый ответ для этого уведомления.", "reply_unavailable")
        self._call(path, interface, "sendReply", "s", (text,), mutating=True)
        return {"status": "requested"}

    @staticmethod
    def _launch(program, *args):
        binary = shutil.which(program)
        if binary is None:
            raise KdeError("Не найдено приложение " + program + ". Установите пакет kdeconnect.", "program_missing")
        try:
            subprocess.Popen([binary, *args], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as error:
            raise KdeError("Не удалось открыть " + program + ".", "launch_failed") from error

    def open_config(self):
        self._launch("kdeconnect-app")

    def open_sms(self, device_id=None):
        if device_id is not None:
            self._path(device_id)
            self._launch("kdeconnect-sms", "--device", str(device_id))
        else:
            self._launch("kdeconnect-sms")
