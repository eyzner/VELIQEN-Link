"""Explicit Bluetooth phone-call control through PipeWire's supported D-Bus API.

No command is sent by construction, refresh(), or subscription.  A call can only
be controlled after its gateway has been matched to a paired BlueZ device.
Public synchronous methods are intended for the application's worker thread.
"""
from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET


class TelephonyError(RuntimeError):
    """A user-displayable call-control error."""


def unpack(value):
    if hasattr(value, "unpack"):
        return unpack(value.unpack())
    if isinstance(value, dict):
        return {k: unpack(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(unpack(v) for v in value)
    return value


def first(value):
    value = unpack(value)
    return value[0] if isinstance(value, tuple) and len(value) == 1 else value


def normalize_address(address: str) -> str:
    address = str(address).strip().upper().replace("_", ":")
    if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
        raise TelephonyError("Неверный Bluetooth-адрес устройства.")
    return address


def normalize_number(number: str) -> str:
    # Spaces, brackets and hyphens are presentation characters only.  Never
    # accept newlines/control characters or multiple HFP AT commands.
    if not isinstance(number, str) or any(ord(c) < 32 for c in number):
        raise TelephonyError("Введите номер телефона без управляющих символов.")
    number = re.sub(r"[ ()\-]", "", number).upper()
    if not re.fullmatch(r"[0-9+*#,ABCD]{1,80}", number):
        raise TelephonyError("Номер должен содержать 1–80 цифр и допустимые знаки + * # ,.")
    return number


class GioBus:
    """Small injectable D-Bus transport; importing this module needs no GI."""

    def __init__(self, kind="session", timeout_ms=1800):
        self.kind = kind
        self.timeout_ms = min(max(int(timeout_ms), 250), 10000)
        self._connection = None

    def _get(self):
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio, GLib
            if self._connection is None:
                bus_type = Gio.BusType.SESSION if self.kind == "session" else Gio.BusType.SYSTEM
                self._connection = Gio.bus_get_sync(bus_type, None)
            return self._connection, Gio, GLib
        except Exception as exc:
            raise TelephonyError("Нет доступа к D-Bus. Нужны python3-gi и пользовательский сеанс Ubuntu.") from exc

    def call(self, service, path, interface, method, signature=None, args=()):
        connection, Gio, GLib = self._get()
        params = GLib.Variant(signature, tuple(args)) if signature else None
        try:
            return first(connection.call_sync(service, path, interface, method,
                                              params, None, Gio.DBusCallFlags.NO_AUTO_START,
                                              self.timeout_ms, None))
        except Exception as exc:
            raise TelephonyError(f"D-Bus: не удалось выполнить {method}. Проверьте подключение телефона.") from exc

    def subscribe(self, service, callback):
        connection, Gio, _ = self._get()
        return connection.signal_subscribe(service, None, None, None, None,
                                            Gio.DBusSignalFlags.NONE,
                                            lambda *args: callback())

    def unsubscribe(self, subscription):
        if self._connection is not None:
            self._connection.signal_unsubscribe(subscription)


class TelephonyManager:
    SERVICE = "org.pipewire.Telephony"
    ROOT = "/org/pipewire/Telephony"
    OM = "org.freedesktop.DBus.ObjectManager"
    GATEWAY = "org.pipewire.Telephony.AudioGateway1"
    CALL = "org.pipewire.Telephony.Call1"
    OLD_GATEWAY = "org.ofono.VoiceCallManager"
    OLD_CALL = "org.ofono.VoiceCall"

    def __init__(self, bus=None, system_bus=None, timeout_ms=1800):
        self._session = bus if bus is not None else GioBus("session", timeout_ms)
        self._system = system_bus if system_bus is not None else GioBus("system", timeout_ms)
        self._lock = threading.RLock()
        self._methods = {}
        self._gateways = {}
        self._calls = {}
        self._subscriptions = []
        self._deadline = None

    def _budget(self):
        return self._deadline is None or time.monotonic() < self._deadline

    def _introspect(self, bus, path):
        key = (id(bus), path)
        if key not in self._methods:
            if not self._budget():
                return {}
            try:
                xml = bus.call(self.SERVICE, path, "org.freedesktop.DBus.Introspectable", "Introspect")
                if not isinstance(xml, str) or len(xml) > 1024 * 1024:
                    raise ValueError("invalid introspection")
                root = ET.fromstring(xml)
                self._methods[key] = {
                    i.attrib["name"]: {m.attrib["name"] for m in i.findall("method")}
                    for i in root.findall("interface") if "name" in i.attrib
                }
            except Exception:
                self._methods[key] = {}
        return self._methods[key]

    def _supports(self, bus, path, interface, method):
        return method in self._introspect(bus, path).get(interface, set())

    def _owner(self, bus):
        if not self._budget():
            return False
        try:
            return bool(bus.call("org.freedesktop.DBus", "/org/freedesktop/DBus",
                                 "org.freedesktop.DBus", "NameHasOwner", "(s)", (self.SERVICE,)))
        except Exception:
            return False

    def bluetooth_devices(self):
        """Known paired BlueZ devices; no scan, pairing or connection is started."""
        try:
            objects = self._system.call("org.bluez", "/", self.OM, "GetManagedObjects")
        except Exception as exc:
            raise TelephonyError("Bluetooth недоступен. Включите Bluetooth и проверьте службу BlueZ.") from exc
        devices = []
        for path, interfaces in list(objects.items())[:256]:
            props = interfaces.get("org.bluez.Device1")
            if not props or not props.get("Paired"):
                continue
            try:
                address = normalize_address(props.get("Address", ""))
            except TelephonyError:
                continue
            devices.append({"path": path, "address": address,
                            "name": str(props.get("Alias") or props.get("Name") or address),
                            "paired": True, "connected": bool(props.get("Connected")),
                            "uuids": list(props.get("UUIDs", []))})
        return devices

    def _object_map(self, bus, path):
        if self._budget() and self._supports(bus, path, self.OM, "GetManagedObjects"):
            try:
                result = bus.call(self.SERVICE, path, self.OM, "GetManagedObjects")
                if isinstance(result, dict):
                    return result
            except Exception:
                pass
        return {}

    def refresh(self):
        with self._lock:
            # Timestamp the beginning of the observation, not delivery to GTK.
            # A queued result must not tear down an audio route created later.
            sampled_at = time.monotonic()
            self._deadline = sampled_at + 8.0
            self._methods.clear()
            self._gateways.clear()
            self._calls.clear()
            result = {"available": False, "backend_available": False, "status": "",
                      "gateways": [], "calls": [], "bluetooth_devices": [],
                      "snapshot_monotonic": sampled_at}
            try:
                devices = self.bluetooth_devices()
                result["bluetooth_devices"] = devices
            except TelephonyError as exc:
                result["status"] = str(exc)
                return result
            by_address = {d["address"]: d for d in devices}
            for bus in (self._session, self._system):
                if not self._owner(bus):
                    continue
                result["backend_available"] = True
                objects = self._object_map(bus, self.ROOT)
                gateways = []
                for path, interfaces in list(objects.items())[:256]:
                    props = interfaces.get(self.GATEWAY) or interfaces.get(self.OLD_GATEWAY)
                    if props is not None:
                        gateways.append((path, props))
                if not gateways and self._budget() and self._supports(bus, self.ROOT, "org.ofono.Manager", "GetModems"):
                    try:
                        gateways = bus.call(self.SERVICE, self.ROOT, "org.ofono.Manager", "GetModems")
                    except Exception:
                        gateways = []
                for path, props in list(gateways)[:12]:
                    if not self._budget():
                        break
                    try:
                        address = normalize_address(props.get("Address", ""))
                    except TelephonyError:
                        continue
                    device = by_address.get(address)
                    if not device or not device["connected"]:
                        continue
                    # Do not mistake another oFono modem for the selected phone.
                    iface = self.GATEWAY if self._supports(bus, path, self.GATEWAY, "Dial") else self.OLD_GATEWAY
                    if not self._supports(bus, path, iface, "Dial"):
                        continue
                    gateway = {"path": path, "address": address, "name": device["name"],
                               "paired": True, "connected": True, "bus": getattr(bus, "kind", "session")}
                    result["gateways"].append(gateway)
                    self._gateways[path] = (bus, iface, gateway)
                    call_objects = dict(objects)
                    call_objects.update(self._object_map(bus, path))
                    found = []
                    for call_path, interfaces in list(call_objects.items())[:256]:
                        call_props = interfaces.get(self.CALL) or interfaces.get(self.OLD_CALL)
                        if call_props is not None and call_path.startswith(path.rstrip("/") + "/"):
                            found.append((call_path, call_props))
                    if not found and self._budget() and self._supports(bus, path, self.OLD_GATEWAY, "GetCalls"):
                        try:
                            found = bus.call(self.SERVICE, path, self.OLD_GATEWAY, "GetCalls")
                        except Exception:
                            found = []
                    for call_path, call_props in list(found)[:24]:
                        if not self._budget():
                            break
                        if not isinstance(call_path, str) or not call_path.startswith(path.rstrip("/") + "/"):
                            continue
                        call_iface = self.CALL if self._supports(bus, call_path, self.CALL, "Hangup") else self.OLD_CALL
                        if not self._supports(bus, call_path, call_iface, "Hangup"):
                            continue
                        call = {"path": call_path, "gateway_path": path, "address": address,
                                "number": str(call_props.get("LineIdentification", "")),
                                "name": str(call_props.get("Name", "")),
                                "state": str(call_props.get("State", "unknown"))}
                        self._calls[call_path] = (bus, call_iface, call)
                        result["calls"].append(call)
                if result["gateways"]:
                    break  # session and system aliases must not duplicate a gateway
            result["available"] = bool(result["gateways"])
            if result["available"]:
                result["status"] = "Управление звонками готово. Звук подключается отдельно после начала разговора."
            elif result["backend_available"]:
                result["status"] = "Нет подключенного HFP-телефона. Сопрягите телефон в настройках Bluetooth и включите звонки для ноутбука."
            else:
                result["status"] = "Служба звонков PipeWire недоступна. Нужны PipeWire 1.4+ и Bluetooth HFP; см. инструкцию «Звонки»."
            if not self._budget():
                result["status"] += " Обновление списка заняло слишком долго; часть устройств не опрошена."
            return result

    def _gateway_action(self, path, method, signature=None, args=()):
        with self._lock:
            previous = self._gateways.get(path)
            self.refresh()
            context = self._gateways.get(path)
            if not context:
                raise TelephonyError("Выбранный телефон больше не подключен по HFP. Обновите список.")
            bus, interface, _ = context
            if previous and previous[2]["address"] != context[2]["address"]:
                raise TelephonyError("Телефон по этому адресу службы изменился. Выберите устройство заново.")
            if method == "Dial" and any(call[2].get("gateway_path") == path
                                        and call[2].get("state") != "disconnected"
                                        for call in self._calls.values()):
                raise TelephonyError("На телефоне уже идёт звонок. Завершите его перед новым вызовом.")
            if not self._supports(bus, path, interface, method):
                raise TelephonyError("Эта операция не поддерживается текущим HFP-соединением.")
            bus.call(self.SERVICE, path, interface, method, signature, args)
            return True

    def dial(self, gateway_path, number):
        return self._gateway_action(gateway_path, "Dial", "(s)", (normalize_number(number),))

    def send_tones(self, gateway_path, tones):
        if not isinstance(tones, str) or not re.fullmatch(r"[0-9*#ABCD]{1,40}", tones.upper()):
            raise TelephonyError("DTMF: допустимы цифры, * и #, не более 40 знаков.")
        return self._gateway_action(gateway_path, "SendTones", "(s)", (tones.upper(),))

    def send_call_tones(self, call_path, tones, *, expected_address, expected_number):
        """Revalidate a captured active call before using gateway-scoped HFP DTMF.

        The provider exposes SendTones on the gateway, not the individual call.
        Validate and dispatch under the same lock, without another refresh that
        could silently select a replacement call or another phone.
        """
        if not isinstance(tones, str) or not re.fullmatch(r"[0-9*#]{1,40}", tones):
            raise TelephonyError("Во время разговора доступны цифры, * и #.")
        expected_address = normalize_address(expected_address)
        with self._lock:
            self.refresh()
            context = self._calls.get(call_path)
            if not context:
                raise TelephonyError("Звонок уже завершён. Цифры не отправлены.")
            _, _, call = context
            if (call.get("state") != "active" or call.get("address") != expected_address
                    or call.get("number", "") != expected_number):
                raise TelephonyError("Текущий звонок изменился. Цифры не отправлены.")
            active = [item[2] for item in self._calls.values()
                      if item[2].get("gateway_path") == call["gateway_path"]
                      and item[2].get("state") == "active"]
            if len(active) != 1 or active[0]["path"] != call_path:
                raise TelephonyError("Для нескольких одновременных разговоров используй клавиатуру телефона.")
            gateway = self._gateways.get(call["gateway_path"])
            if not gateway or gateway[2].get("address") != expected_address:
                raise TelephonyError("Телефон отключился. Цифры не отправлены.")
            bus, interface, _ = gateway
            if not self._supports(bus, call["gateway_path"], interface, "SendTones"):
                raise TelephonyError("Этот телефон не передаёт нажатия клавиш через Bluetooth.")
            bus.call(self.SERVICE, call["gateway_path"], interface, "SendTones", "(s)", (tones,))
            return True

    def _call_action(self, path, method):
        with self._lock:
            previous = self._calls.get(path)
            self.refresh()
            context = self._calls.get(path)
            if not context:
                raise TelephonyError("Звонок уже завершен или телефон отключен.")
            bus, interface, call = context
            if previous and any(previous[2][key] != call[key] for key in ("address", "number")):
                raise TelephonyError("Данные звонка изменились. Обновите список и выберите звонок заново.")
            if method == "Answer" and call["state"] != "incoming":
                raise TelephonyError("Этот звонок не ожидает ответа. Для второй линии используйте телефон.")
            if call["state"] == "disconnected":
                raise TelephonyError("Звонок уже завершен.")
            if not self._supports(bus, path, interface, method):
                raise TelephonyError("Операция недоступна в текущей версии PipeWire.")
            bus.call(self.SERVICE, path, interface, method)
            return True

    def answer(self, call_path):
        return self._call_action(call_path, "Answer")

    def hangup(self, call_path):
        return self._call_action(call_path, "Hangup")

    def connect_bluetooth(self, address):
        address = normalize_address(address)
        with self._lock:
            device = next((d for d in self.bluetooth_devices() if d["address"] == address), None)
            if not device:
                raise TelephonyError("Сначала выполните сопряжение в настройках Bluetooth Ubuntu.")
            self._system.call("org.bluez", device["path"], "org.bluez.Device1", "Connect")
            return True

    def watch(self, callback):
        """Subscribe to invalidations on GLib's main loop; callback has no args.

        The callback must schedule refresh() in a worker.  Periodic refresh is
        still useful when a service is first installed or appears after login.
        """
        for bus, service in ((self._session, self.SERVICE), (self._system, self.SERVICE), (self._system, "org.bluez")):
            try:
                token = bus.subscribe(service, callback)
                self._subscriptions.append((bus, token))
            except Exception:
                continue

    def close(self):
        for bus, token in self._subscriptions:
            try:
                bus.unsubscribe(token)
            except Exception:
                pass
        self._subscriptions.clear()
