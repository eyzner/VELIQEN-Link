"""Bluetooth setup without a private pairing agent or hidden acceptance.

All mutations are explicit methods for a GTK worker. snapshot() only reads.
Pair() deliberately uses the desktop's normal BlueZ agent, so its matching-code
confirmation remains visible. A missing agent falls back to system Settings.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import unicodedata

from .telephony import first, normalize_address, TelephonyError


HFP_AG_UUID = "0000111f-0000-1000-8000-00805f9b34fb"
HSP_AG_UUID = "00001112-0000-1000-8000-00805f9b34fb"


class BluetoothError(RuntimeError):
    def __init__(self, message, code="failed", detail=""):
        super().__init__(message)
        self.code = code
        self.detail = detail


def _friendly_name(value):
    text = "".join(c for c in str(value or "") if unicodedata.category(c)[0] != "C")
    return text.strip()[:160]


def _match_name(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


class BlueZBus:
    """Small injectable transport, preserving remote errors for useful guidance."""
    def __init__(self):
        self._connection = None

    def call(self, path, interface, method, signature=None, args=(), timeout_ms=2500):
        try:
            from gi.repository import Gio, GLib
            if self._connection is None:
                self._connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
            params = GLib.Variant(signature, tuple(args)) if signature else None
            return first(self._connection.call_sync(
                "org.bluez", path, interface, method, params, None,
                Gio.DBusCallFlags.NO_AUTO_START, timeout_ms, None))
        except Exception as exc:
            remote = ""
            try:
                remote = Gio.DBusError.get_remote_error(exc) or ""
            except (NameError, TypeError, AttributeError):
                pass
            detail = str(exc)
            code = remote.rsplit(".", 1)[-1] or "failed"
            raise BluetoothError("Bluetooth не ответил. Открой настройки Bluetooth и попробуй ещё раз.", code, detail) from exc

    def set_powered(self, path):
        from gi.repository import GLib
        return self.call(path, "org.freedesktop.DBus.Properties", "Set", "(ssv)",
                         ("org.bluez.Adapter1", "Powered", GLib.Variant("b", True)))


class BluetoothManager:
    OM = "org.freedesktop.DBus.ObjectManager"
    ADAPTER = "org.bluez.Adapter1"
    DEVICE = "org.bluez.Device1"

    def __init__(self, bus=None, which=None, launcher=None):
        self._bus = bus or BlueZBus()
        self._which = which or shutil.which
        self._launcher = launcher or subprocess.Popen
        self._discovery_owned = set()
        self._pairing_owned = set()
        self._lock = threading.RLock()

    def snapshot(self):
        result = {"available": False, "powered": False, "discovering": False,
                  "adapters": [], "devices": [], "phones": [],
                  "state": "unavailable", "status": "Bluetooth недоступен на этом компьютере."}
        try:
            objects = self._bus.call("/", self.OM, "GetManagedObjects")
            if not isinstance(objects, dict):
                return result
        except Exception:
            result["status"] = "Не удалось прочитать состояние Bluetooth. Открой его системные настройки."
            return result
        for path, interfaces in list(objects.items())[:1024]:
            props = interfaces.get(self.ADAPTER)
            if props is not None:
                result["adapters"].append({"path": path,
                    "name": _friendly_name(props.get("Alias") or props.get("Name")) or "Bluetooth-адаптер",
                    "powered": bool(props.get("Powered")), "discovering": bool(props.get("Discovering"))})
        result["available"] = bool(result["adapters"])
        result["powered"] = any(a["powered"] for a in result["adapters"])
        result["discovering"] = any(a["discovering"] for a in result["adapters"])
        adapter_power = {a["path"]: a["powered"] for a in result["adapters"]}
        devices = {}
        for path, interfaces in list(objects.items())[:1024]:
            props = interfaces.get(self.DEVICE)
            if not props:
                continue
            try:
                address = normalize_address(props.get("Address", ""))
            except TelephonyError:
                continue
            name = _friendly_name(props.get("Alias") or props.get("Name"))
            # A Bluetooth address is retained as an identifier, never the primary label.
            if not name or _match_name(name) == _match_name(address) or name.upper() == address.replace(":", "-"):
                name = "Устройство без имени"
            uuids = [str(u).lower() for u in props.get("UUIDs", [])]
            icon = str(props.get("Icon", "")).lower()
            try:
                major_class = (int(props.get("Class", 0)) >> 8) & 0x1F
            except (ValueError, TypeError):
                major_class = 0
            phone = (icon in {"phone", "smartphone"} or major_class == 2
                     or HFP_AG_UUID in uuids or HSP_AG_UUID in uuids)
            known_other = not phone and (major_class not in (0, 2) or
                icon.startswith(("audio", "input", "computer", "camera", "printer")))
            item = {"path": path, "address": address, "name": name,
                    "paired": bool(props.get("Paired")), "connected": bool(props.get("Connected")),
                    "blocked": bool(props.get("Blocked")), "phone": phone, "uuids": uuids,
                    "kind": "phone" if phone else "other" if known_other else "unknown",
                    "adapter": str(props.get("Adapter", path.rsplit("/", 1)[0])),
                    "services_resolved": bool(props.get("ServicesResolved"))}
            item["adapter_powered"] = adapter_power.get(item["adapter"], False)
            previous = devices.get(address)
            rank = lambda d: (d["connected"], d["adapter_powered"], d["paired"])
            if previous is None or rank(item) > rank(previous):
                devices[address] = item
        result["devices"] = sorted(devices.values(), key=lambda d: (not d["paired"], not d["connected"], d["name"].casefold(), d["address"]))
        result["phones"] = [d for d in result["devices"] if d["phone"] and not d["blocked"]]
        if not result["available"]:
            return result
        if not result["powered"]:
            result.update(state="off", status="Включи Bluetooth, чтобы принимать звонки на компьютере.")
        elif result["discovering"]:
            result.update(state="searching", status="Ищем телефон. Оставь настройки Bluetooth телефона открытыми.")
        elif any(d["paired"] for d in result["phones"]):
            result.update(state="available", status="Выбери телефон для звонков.")
        else:
            result.update(state="available", status="Подключи телефон по Bluetooth один раз — затем он будет узнаваемым.")
        return result

    @staticmethod
    def suggest_phone(snapshot, kde_name="", remembered_address=None):
        """Suggest a device; a name mismatch always requires visible confirmation.

        A suggestion is not proof that the KDE and Bluetooth identities match.
        Callers should save an address only after the user chooses/accepts it.
        """
        devices = [d for d in snapshot.get("devices", []) if d.get("paired") and not d.get("blocked") and d.get("kind") != "other"]
        selected = None
        if remembered_address:
            try:
                address = normalize_address(remembered_address)
                selected = next((d for d in devices if d["address"] == address), None)
            except TelephonyError:
                pass
        if selected:
            return {"device": selected, "reason": "remembered", "needs_confirmation": False, "choices": [selected]}
        target = _match_name(kde_name)
        exact = [d for d in devices if target and _match_name(d["name"]) == target]
        if len(exact) == 1:
            return {"device": exact[0], "reason": "name", "needs_confirmation": False, "choices": exact}
        if len(exact) > 1:
            return {"device": None, "reason": "ambiguous", "needs_confirmation": True, "choices": exact}
        phones = [d for d in devices if d.get("phone")]
        if len(phones) == 1:
            return {"device": phones[0], "reason": "single_phone", "needs_confirmation": True, "choices": phones}
        return {"device": None, "reason": "ambiguous" if phones else "none", "needs_confirmation": True, "choices": phones}

    def _device(self, path):
        if not isinstance(path, str) or not re.fullmatch(r"/org/bluez/hci[0-9]+/dev_(?:[0-9A-Fa-f]{2}_){5}[0-9A-Fa-f]{2}", path):
            raise BluetoothError("Выбери телефон из списка.", "unknown_device")
        state = self.snapshot()
        device = next((d for d in state["devices"] if d["path"] == path), None)
        if not device:
            raise BluetoothError("Телефон больше не виден. Обнови список устройств.", "unknown_device")
        if device["blocked"]:
            raise BluetoothError("Телефон заблокирован в настройках Bluetooth. Разреши подключение там.", "blocked")
        if device.get("kind") == "other":
            raise BluetoothError("Выбери телефон, а не наушники или другое устройство.", "not_phone")
        if not device["adapter_powered"]:
            raise BluetoothError("Bluetooth выключен. Включи его в настройках компьютера.", "off")
        return device

    @staticmethod
    def _failure(exc, stage="connect"):
        code = getattr(exc, "code", "failed")
        detail = getattr(exc, "detail", str(exc)).lower()
        if "agent" in detail and any(part in detail for part in ("no ", "not ", "available", "registered")):
            return "needs_settings", "Подтверди подключение через системные настройки Bluetooth."
        messages = {
            "AuthenticationCanceled": "Сопряжение отменено. Нажми подключение снова, когда будешь готов.",
            "AuthenticationRejected": "Подключение не подтверждено. Проверь, что код совпадает на телефоне и компьютере.",
            "AuthenticationFailed": "Не удалось подтвердить устройства. Повтори сопряжение в настройках Bluetooth.",
            "AuthenticationTimeout": "Время подтверждения истекло. Держи телефон разблокированным и повтори подключение.",
            "NotReady": "Bluetooth пока выключен или не готов. Открой настройки Bluetooth.",
            "NotAvailable": "Сопряжение есть, но звонки ещё недоступны. Разреши звонки для компьютера в настройках Bluetooth телефона.",
            "NotSupported": "Этот телефон пока не предоставляет подключение для звонков.",
            "ConnectionAttemptFailed": "Не удалось связаться с телефоном. Поднеси его ближе и открой настройки Bluetooth.",
            "InProgress": "Подключение уже выполняется. Подтверди запрос на телефоне и немного подожди.",
            "NoReply": "Телефон не ответил. Открой его настройки Bluetooth и повтори подключение.",
            "Timeout": "Подключение заняло слишком много времени. Повтори его с разблокированным телефоном.",
            "blocked": "Телефон заблокирован в настройках Bluetooth. Разреши подключение там.",
            "off": "Включи Bluetooth в настройках компьютера.",
            "unknown_device": "Телефон больше не виден. Обнови список устройств.",
            "not_phone": "Для звонков выбери телефон. Наушники подключаются в настройках звука Ubuntu.",
        }
        if code in {"AuthenticationCanceled", "AuthenticationRejected"}:
            state = "cancelled"
        elif code == "InProgress":
            state = "pending"
        elif code in {"NotReady", "off"}:
            state = "off"
        elif stage == "profile" and code in {"NotAvailable", "NotSupported", "Failed"}:
            state = "needs_call_setup"
        else:
            state = "failed"
        return state, messages.get(code, "Подключение не удалось. Проверь Bluetooth на телефоне и повтори попытку.")

    def open_settings(self):
        commands = [("gnome-control-center", "bluetooth"), ("blueman-manager",),
                    ("systemsettings", "kcm_bluetooth"), ("systemsettings5", "kcm_bluetooth")]
        for command in commands:
            executable = self._which(command[0])
            if not executable:
                continue
            try:
                self._launcher([executable, *command[1:]], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"state": "settings_opened", "message": "Выбери телефон в открывшихся настройках и подтверди одинаковый код на обоих устройствах."}
            except OSError:
                continue
        return {"state": "settings_unavailable", "message": "Открой системные настройки Bluetooth, выбери свой телефон и подтверди одинаковый код на обоих устройствах."}

    def connect_phone(self, path):
        """User-requested pairing and call-profile connection; never dials a call."""
        try:
            device = self._device(path)
        except BluetoothError as exc:
            state, message = self._failure(exc)
            return {"state": state, "message": message, "device": None}
        if not device["paired"]:
            with self._lock:
                if path in self._pairing_owned:
                    return {"state": "pending", "message": "Подтверди код на телефоне и компьютере.", "device": device}
                self._pairing_owned.add(path)
            try:
                # No RegisterAgent call: BlueZ uses the trusted desktop agent.
                self._bus.call(path, self.DEVICE, "Pair", timeout_ms=45000)
            except Exception as exc:
                if getattr(exc, "code", "") != "AlreadyExists":
                    state, message = self._failure(exc, "pair")
                    if state == "needs_settings":
                        settings = self.open_settings()
                        return {**settings, "device": device}
                    return {"state": state, "message": message, "device": device}
            finally:
                with self._lock:
                    self._pairing_owned.discard(path)
            try:
                device = self._device(path)
            except BluetoothError as exc:
                state, message = self._failure(exc)
                return {"state": state, "message": message, "device": None}
            if not device["paired"]:
                return {"state": "pending", "message": "Ждём завершения сопряжения. Подтверди запрос на телефоне.", "device": device}
        try:
            self._bus.call(path, self.DEVICE, "ConnectProfile", "(s)", (HFP_AG_UUID,), timeout_ms=20000)
        except Exception as exc:
            if getattr(exc, "code", "") != "AlreadyConnected":
                state, message = self._failure(exc, "profile")
                return {"state": state, "message": message, "device": device}
        # A successful ConnectProfile is not proof that a live call gateway or
        # microphone route is ready. The caller must refresh TelephonyManager.
        return {"state": "connected", "message": "Телефон подключён по Bluetooth. Проверяем готовность звонков…", "device": device}

    def cancel_pairing(self, path):
        with self._lock:
            ours = path in self._pairing_owned
        if not ours:
            return False
        try:
            self._bus.call(path, self.DEVICE, "CancelPairing")
            return True
        except BluetoothError:
            return False

    def power_on(self, adapter_path=None):
        state = self.snapshot()
        adapters = state["adapters"]
        if adapter_path:
            adapters = [a for a in adapters if a["path"] == adapter_path]
        if not adapters:
            return {"state": "unavailable", "message": "Bluetooth-адаптер не найден."}
        target = next((a for a in adapters if a["powered"]), adapters[0])
        if not target["powered"]:
            try:
                self._bus.set_powered(target["path"])
            except Exception:
                return {"state": "off", "message": "Включи Bluetooth в системных настройках. Возможно, включён режим полёта."}
        return {"state": "powered", "message": "Bluetooth включён.", "adapter": target["path"]}

    def start_discovery(self, adapter_path=None):
        state = self.snapshot()
        adapters = [a for a in state["adapters"] if a["powered"] and (not adapter_path or a["path"] == adapter_path)]
        if not adapters:
            return {"state": "off", "message": "Сначала включи Bluetooth на компьютере."}
        path = adapters[0]["path"]
        with self._lock:
            if path in self._discovery_owned:
                return {"state": "searching", "message": "Ищем телефон. Оставь его настройки Bluetooth открытыми."}
            try:
                self._bus.call(path, self.ADAPTER, "StartDiscovery")
            except Exception as exc:
                kind, message = self._failure(exc)
                return {"state": kind, "message": message}
            self._discovery_owned.add(path)
        return {"state": "searching", "message": "Ищем телефон. Оставь его настройки Bluetooth открытыми."}

    def stop_discovery(self):
        with self._lock:
            owned = list(self._discovery_owned)
        for path in owned:
            try:
                self._bus.call(path, self.ADAPTER, "StopDiscovery")
            except Exception:
                continue
            with self._lock:
                self._discovery_owned.discard(path)

    def close(self):
        self.stop_discovery()
        with self._lock:
            pending = list(self._pairing_owned)
        for path in pending:
            self.cancel_pairing(path)


def suggest_phone(snapshot, kde_name="", remembered_address=None):
    return BluetoothManager.suggest_phone(snapshot, kde_name, remembered_address)
