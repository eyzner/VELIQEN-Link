"""Explicit, narrow firewall setup for an already-paired reachable phone.

No LAN probing, fabricated address, shell, silent privilege change or invocation
of a user-writable source helper. Root-owned installed helper validates again.
"""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
import stat
import subprocess


INSTALLED_HELPER = Path("/usr/share/veliqen-link/scripts/ALLOW-SCREEN.py")
PKEXEC = "/usr/bin/pkexec"
PYTHON = "/usr/bin/python3"
PRIVATE = tuple(ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class NetworkError(RuntimeError):
    pass


def validate_address(value):
    if not isinstance(value, str) or value != value.strip():
        raise NetworkError("Не удалось определить адрес телефона в Wi-Fi.")
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise NetworkError("Нужен локальный IPv4-адрес телефона без порта и маски сети.") from exc
    if not any(address in network for network in PRIVATE):
        raise NetworkError("Настройка доступна только для телефона в локальной Wi-Fi-сети.")
    return str(address)


def screen_address(device):
    """KDE reachableAddresses contains bare LAN IPs, plus other transports.

    Source: LanDeviceLink.address() returns QHostAddress.toString(); this is not
    a URL and must never be parsed by extracting numbers from arbitrary text.
    """
    if not isinstance(device, dict) or device.get("paired") is not True:
        raise NetworkError("Сначала подтверди подключение телефона к компьютеру.")
    if device.get("reachable") is not True:
        raise NetworkError("Телефон сейчас недоступен. Подключи его к той же Wi-Fi-сети.")
    values = device.get("addresses", [])
    if not isinstance(values, (list, tuple)):
        raise NetworkError("Адрес телефона пока неизвестен. Обнови подключение.")
    addresses = []
    for value in values:
        if not isinstance(value, str) or len(value) > 80:
            continue
        try:
            parsed = ipaddress.ip_address(value)
            if isinstance(parsed, ipaddress.IPv6Address):
                parsed = parsed.ipv4_mapped
            if parsed is None or not any(parsed in network for network in PRIVATE):
                continue
            address = str(parsed)
            if address not in addresses:
                addresses.append(address)
        except ValueError:
            continue
    if not addresses:
        raise NetworkError("Не найден локальный IPv4-адрес телефона. Подключи оба устройства к одной Wi-Fi-сети и обнови подключение.")
    if len(addresses) != 1:
        raise NetworkError("У телефона несколько сетевых подключений. Оставь подключение к общей Wi-Fi-сети и попробуй ещё раз.")
    return addresses[0]


def _installed_helper_is_trusted():
    # All path components must be root-owned and non-writable by other users.
    # This excludes a symlink to a source checkout, even if its target exists.
    try:
        paths = [INSTALLED_HELPER, *INSTALLED_HELPER.parents]
        for index, path in enumerate(paths):
            info = path.lstat()
            expected_type = stat.S_ISREG if index == 0 else stat.S_ISDIR
            if not expected_type(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                return False
        return True
    except OSError:
        return False


def open_screen_port(address, *, runner=None):
    """Call only after the user presses the setup action and sees its purpose.

    The OS supplies its own authentication dialog. No password is handled by
    VELIQEN Link. The only requested rule is TCP 38475 from this exact phone IP.
    """
    address = validate_address(address)
    if not _installed_helper_is_trusted():
        return {"state": "missing_helper", "message": "Установи обновлённый пакет VELIQEN Link: компонент настройки сети ещё не установлен.", "address": address}
    if not os.access(PKEXEC, os.X_OK):
        return {"state": "unavailable", "message": "Системное подтверждение настройки недоступно. Повтори установку VELIQEN Link полным установщиком.", "address": address}
    run = runner or subprocess.run
    command = [PKEXEC, PYTHON, "-I", str(INSTALLED_HELPER), "--address", address]
    try:
        result = run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False, timeout=60)
    except subprocess.TimeoutExpired:
        return {"state": "unknown", "message": "Подтверждение настройки не получено вовремя. Автоматического повтора не будет.", "address": address}
    except OSError:
        return {"state": "unavailable", "message": "Не удалось открыть системное подтверждение настройки.", "address": address}
    if result.returncode in (126, 127):
        return {"state": "cancelled", "message": "Настройка не подтверждена. Можно повторить её, когда будешь готов.", "address": address}
    output = (result.stdout or "")
    try:
        value = json.loads(output) if len(output) <= 16384 else None
    except (ValueError, TypeError):
        value = None
    allowed_states = {"allowed", "inactive", "not_installed", "error"}
    if isinstance(value, dict) and value.get("state") in allowed_states and isinstance(value.get("message"), str):
        if value.get("address", address) != address:
            raise NetworkError("Настройка вернула другой адрес телефона. Повтори проверку подключения.")
        # A non-zero helper exit cannot be promoted to a successful result.
        if result.returncode != 0 and value["state"] != "error":
            raise NetworkError("Не удалось подтвердить результат настройки сети.")
        return {"state": value["state"], "message": value["message"][:1000], "address": address}
    return {"state": "failed", "message": "Система не подтвердила настройку сети. Правила автоматически повторно не меняются.", "address": address}
