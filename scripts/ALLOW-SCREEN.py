#!/usr/bin/python3
"""Narrow privileged UFW action for one observed phone's private IPv4.

Installed by the .deb as a root-owned file. Never enables/disables UFW and never
accepts a network, port, command, shell fragment, or arbitrary executable path.
SPDX-License-Identifier: GPL-3.0-or-later
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys

UFW = "/usr/sbin/ufw"
PRIVATE = tuple(ipaddress.IPv4Network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))
COMMAND_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LC_ALL": "C", "LANG": "C", "PYTHONNOUSERSITE": "1"}


class RuleError(RuntimeError):
    pass


def validate_address(value):
    if not isinstance(value, str) or value != value.strip():
        raise RuleError("Нужен один локальный IPv4-адрес телефона.")
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, ValueError) as exc:
        raise RuleError("Нужен IPv4-адрес телефона без порта, маски сети и дополнительных команд.") from exc
    if not any(address in network for network in PRIVATE):
        raise RuleError("Разрешены только локальные адреса телефона из сети Wi-Fi.")
    return str(address)


def apply_rule(value, *, runner=None, euid=None, exists=None):
    """Injectable for tests; all production privileged arguments are fixed here."""
    if (os.geteuid() if euid is None else euid) != 0:
        raise RuleError("Изменение правил требует подтверждения системного администратора.")
    address = validate_address(value)
    exists = exists or (lambda path: Path(path).is_file())
    if not exists(UFW):
        return {"state": "not_installed", "message": "Этот способ настройки нужен для UFW. На компьютере он не установлен; правила не менялись.", "address": address}
    run = runner or subprocess.run
    try:
        status = run([UFW, "status"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, check=False, timeout=10, env=COMMAND_ENV)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuleError("Не удалось проверить межсетевой экран. Его правила не менялись.") from exc
    if status.returncode:
        raise RuleError("Межсетевой экран не подтвердил своё состояние. Правила не менялись.")
    match = re.search(r"^Status:\s*(active|inactive)\s*$", status.stdout or "", re.MULTILINE)
    if not match:
        raise RuleError("Состояние межсетевого экрана не распознано. Правила не менялись.")
    if match.group(1) == "inactive":
        return {"state": "inactive", "message": "UFW выключен и не блокирует показ экрана. Ничего менять не нужно.", "address": address}
    command = [UFW, "allow", "from", address, "to", "any", "port", "38475", "proto", "tcp", "comment", "VELIQEN Link Screen"]
    try:
        result = run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                     text=True, check=False, timeout=15, env=COMMAND_ENV)
    except subprocess.TimeoutExpired as exc:
        raise RuleError("Настройка не ответила вовремя. Результат изменения неизвестен; автоматического повтора не будет.") from exc
    except OSError as exc:
        raise RuleError("Не удалось выполнить настройку межсетевого экрана.") from exc
    if result.returncode:
        raise RuleError("Межсетевой экран не подтвердил добавление правила. Автоматического повтора не будет.")
    return {"state": "allowed", "message": "Показ экрана разрешён для этого телефона. Нажми «Показать экран» ещё раз.", "address": address}


def main(arguments=None):
    parser = argparse.ArgumentParser(description="Разрешить показ экрана только от выбранного телефона.", allow_abbrev=False)
    parser.add_argument("--address", required=True)
    args = parser.parse_args(arguments)
    try:
        result = apply_rule(args.address)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except RuleError as exc:
        print(json.dumps({"state": "error", "message": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
