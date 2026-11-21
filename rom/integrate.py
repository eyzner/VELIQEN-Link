#!/usr/bin/env python3
"""Reversible source-tree integration. Never operates on a connected device.

SPDX-License-Identifier: GPL-3.0-or-later
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import sys
import tempfile
from datetime import datetime, timezone
import uuid
import zipfile

HERE = Path(__file__).resolve().parent
RELATIVE_DEST = Path("vendor/veliqen/link")
STATE_NAME = ".veliqen-link-owned.json"
BACKUP_DIR = ".veliqen-link-backups"
BLOCK = ("# BEGIN VELIQEN LINK (managed)\n"
         "$(call inherit-product, vendor/veliqen/link/product.mk)\n"
         "# END VELIQEN LINK (managed)\n").encode()


def fail(message: str) -> None:
    raise RuntimeError(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_atomic(path: Path, data: bytes, mode: int = 0o644) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_json(path: Path, data: dict) -> None:
    write_atomic(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def within_tree(tree: Path, path: Path) -> Path:
    if not path.is_absolute() or not path.is_relative_to(tree):
        fail(f"Путь должен находиться внутри исходников: {path}")
    resolved = path.resolve()
    if not resolved.is_relative_to(tree):
        fail(f"Путь выходит за пределы исходников: {path}")
    # Do not mutate through a symlink, even if it currently points within the tree.
    current = path
    while current != tree:
        if current == current.parent:
            fail(f"Невозможно проверить путь: {path}")
        if current.is_symlink():
            fail(f"Символические ссылки в изменяемом пути не поддерживаются: {current}")
        current = current.parent
    return resolved


def validate_tree(raw: str) -> Path:
    tree = Path(raw).expanduser().resolve()
    if not (tree / "build/envsetup.sh").is_file():
        fail("Не найден build/envsetup.sh. Укажите корень исходников AOSP/LineageOS.")
    within_tree(tree, tree / RELATIVE_DEST)
    within_tree(tree, tree / BACKUP_DIR)
    return tree


def file_map(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for file in sorted(path.rglob("*")):
        if file.is_symlink():
            fail(f"Символическая ссылка в управляемом каталоге: {file}")
        if file.is_file() and file != path / STATE_NAME:
            result[file.relative_to(path).as_posix()] = digest(file.read_bytes())
    return result


def load_owned(path: Path) -> dict:
    if not path.is_dir() or not (path / STATE_NAME).is_file():
        fail(f"{path} уже существует и не принадлежит этому установщику. Ничего не заменено.")
    state = json.loads((path / STATE_NAME).read_text())
    if state.get("owner") != "veliqen-link" or file_map(path) != state.get("files"):
        fail(f"В {path} есть изменения после установки. Сохраните/разберите их отдельно; перезапись запрещена.")
    return state


def apk_package(apk: Path) -> str:
    """Read only the package attribute from Android's binary XML manifest."""
    with zipfile.ZipFile(apk) as archive:
        names = archive.namelist()
        if "classes.dex" not in names or "AndroidManifest.xml" not in names:
            fail("Файл не содержит AndroidManifest.xml и classes.dex: это не APK клиента.")
        info = archive.getinfo("AndroidManifest.xml")
        if info.file_size > 4 * 1024 * 1024:
            fail("Manifest APK имеет неожиданно большой размер.")
        data = archive.read(info)
    if len(data) < 8 or struct.unpack_from("<H", data)[0] != 0x0003:
        fail("Ожидался скомпилированный Android APK с binary XML manifest.")
    strings: list[str] = []
    offset = 8
    while offset + 8 <= len(data):
        chunk_type, header_size, chunk_size = struct.unpack_from("<HHI", data, offset)
        if chunk_size < header_size or chunk_size < 8 or offset + chunk_size > len(data):
            fail("Повреждённый binary XML manifest APK.")
        if chunk_type == 0x0001:
            count, _, flags, strings_start, _ = struct.unpack_from("<IIIII", data, offset + 8)
            if count > 100000:
                fail("Неожиданно большая таблица строк APK.")
            for i in range(count):
                relative = struct.unpack_from("<I", data, offset + header_size + i * 4)[0]
                position = offset + strings_start + relative
                if flags & 0x100:
                    # UTF-8 string pool: UTF-16 length followed by byte length.
                    length = data[position]
                    position += 2 if length & 0x80 else 1
                    length = data[position]
                    if length & 0x80:
                        length = ((length & 0x7f) << 8) | data[position + 1]
                        position += 2
                    else:
                        position += 1
                    strings.append(data[position:position + length].decode("utf-8"))
                else:
                    length = struct.unpack_from("<H", data, position)[0]
                    position += 2
                    if length & 0x8000:
                        length = ((length & 0x7fff) << 16) | struct.unpack_from("<H", data, position)[0]
                        position += 2
                    strings.append(data[position:position + length * 2].decode("utf-16le"))
        elif chunk_type == 0x0102:
            name = struct.unpack_from("<I", data, offset + 20)[0]
            attribute_start, attribute_size, count = struct.unpack_from("<HHH", data, offset + 24)
            if name < len(strings) and strings[name] == "manifest":
                if attribute_size < 20:
                    fail("Некорректные атрибуты AndroidManifest.xml.")
                for i in range(count):
                    position = offset + 16 + attribute_start + i * attribute_size
                    _, attribute_name, raw = struct.unpack_from("<III", data, position)
                    if attribute_name < len(strings) and strings[attribute_name] == "package":
                        if raw != 0xffffffff:
                            return strings[raw]
                        value_type = data[position + 15]
                        value = struct.unpack_from("<I", data, position + 16)[0]
                        if value_type == 0x03:
                            return strings[value]
                        fail("Package ID APK не является строкой.")
        offset += chunk_size
    fail("Не удалось прочитать package ID APK.")


def pick_apk(raw: str | None) -> Path:
    if raw:
        apk = Path(raw).expanduser().resolve()
    else:
        candidates = [HERE.parent / "android/build-output" / f"VELIQEN-Link-1.2.1-{kind}.apk"
                      for kind in ("unsigned", "release")]
        candidates.append(HERE / "payload/prebuilt/VELIQENLink.apk")
        apk = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
    if not apk.is_file():
        fail("APK не найден. Соберите android/BUILD-APK.sh или укажите --apk /путь/к/клиенту.apk.")
    if apk_package(apk) != "tech.veliqen.link":
        fail("Package ID APK должен быть tech.veliqen.link (вариант veliqen, без .debug).")
    return apk


def desired_files(apk: Path) -> dict[str, bytes]:
    files = {p.relative_to(HERE / "payload").as_posix(): p.read_bytes()
             for p in (HERE / "payload").rglob("*") if p.is_file()}
    files["prebuilt/VELIQENLink.apk"] = apk.read_bytes()
    return files


def install(args: argparse.Namespace) -> None:
    tree = validate_tree(args.tree)
    product_raw = Path(args.product_mk).expanduser()
    product = within_tree(tree, product_raw if product_raw.is_absolute() else tree / product_raw)
    if not product.is_file() or product.suffix != ".mk":
        fail("--product-mk должен указывать на существующий .mk выбранного продукта внутри дерева.")
    destination = tree / RELATIVE_DEST
    old_state = load_owned(destination) if destination.exists() else None
    apk = pick_apk(args.apk)
    files = desired_files(apk)
    expected = {name: digest(data) for name, data in files.items()}
    original = product.read_bytes()
    if BLOCK in original:
        if original.count(BLOCK) != 1:
            fail("В product mk найдено несколько managed-блоков VELIQEN Link.")
        after = original
        added_block = False
    else:
        if b"vendor/veliqen/link/product.mk" in original or b"BEGIN VELIQEN LINK" in original or b"END VELIQEN LINK" in original:
            fail("В product mk уже есть ручное или изменённое подключение VELIQEN Link. Исправьте его явно.")
        after = original + (b"\n" if original and not original.endswith(b"\n") else b"") + b"\n" + BLOCK
        added_block = True
    if old_state and old_state["files"] == expected and after == original:
        print("VELIQEN Link уже подключён с тем же APK. Изменений нет.")
        return
    print(f"Дерево: {tree}\nПродукт: {product.relative_to(tree)}\nAPK: {apk}")
    print(f"SHA-256 APK: {digest(apk.read_bytes())}")
    print(f"Записать {len(files)} файлов в {RELATIVE_DEST}; добавить include: {added_block}.")
    if args.dry_run:
        print("DRY RUN: файлы не изменены; резервная копия не создавалась.")
        return

    transaction = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup = tree / BACKUP_DIR / transaction
    backup.mkdir(parents=True, mode=0o700)
    os.chmod(tree / BACKUP_DIR, 0o700)
    write_atomic(backup / "product-before.mk", original)
    product_mode = stat.S_IMODE(product.stat().st_mode)
    record = {
        "owner": "veliqen-link", "version": "1.2.1", "transaction": transaction,
        "tree": str(tree), "product": product.relative_to(tree).as_posix(),
        "product_mode": product_mode, "product_before_sha256": digest(original),
        "product_after_sha256": digest(after), "added_block": added_block,
        "destination_existed": bool(old_state), "installed_files": expected, "status": "preparing",
    }
    write_json(backup / "transaction.json", record)
    destination.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".link-stage-", dir=destination.parent))
    payload_moved = False
    replacement_installed = False
    try:
        for relative, data in files.items():
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(target, data)
        write_json(staged / STATE_NAME, {"owner": "veliqen-link", "transaction": transaction, "files": expected})
        if destination.exists():
            destination.rename(backup / "payload-before")
            payload_moved = True
        staged.rename(destination)
        replacement_installed = True
        write_atomic(product, after, product_mode)
        record["status"] = "committed"
        write_json(backup / "transaction.json", record)
    except BaseException:
        if replacement_installed and destination.exists():
            shutil.rmtree(destination)
        if payload_moved:
            (backup / "payload-before").rename(destination)
        write_atomic(product, original, product_mode)
        record["status"] = "failed-and-restored"
        write_json(backup / "transaction.json", record)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    print(f"Подключено. Транзакция: {transaction}")
    print("Следующий шаг: ваша обычная сборка ROM; модуль VELIQENLink подписывается вашим platform key.")
    print(f"Откат: bash ROLLBACK-ROM.sh --tree '{tree}' --transaction {transaction} --dry-run")


def rollback(args: argparse.Namespace) -> None:
    tree = validate_tree(args.tree)
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}", args.transaction):
        fail("Некорректный идентификатор транзакции.")
    backup = within_tree(tree, tree / BACKUP_DIR / args.transaction)
    record = json.loads((backup / "transaction.json").read_text())
    if record.get("tree") != str(tree) or record.get("owner") != "veliqen-link":
        fail("Резервная копия принадлежит другому дереву или инструменту.")
    if record.get("status") == "rolled-back":
        print("Эта транзакция уже отменена. Изменений нет.")
        return
    if record.get("status") != "committed":
        fail("Транзакция не завершена успешно. Сначала проверьте резервную копию вручную.")
    destination = tree / RELATIVE_DEST
    state = load_owned(destination)
    if state.get("transaction") != args.transaction or state["files"] != record["installed_files"]:
        fail("Сначала отмените более позднюю транзакцию. Изменённый каталог не заменяется.")
    original = (backup / "product-before.mk").read_bytes()
    if digest(original) != record["product_before_sha256"]:
        fail("Резервная копия product mk изменена; автоматический откат остановлен.")
    product = within_tree(tree, tree / record["product"])
    current = product.read_bytes()
    if digest(current) == record["product_after_sha256"]:
        restored_product = original
    elif record["added_block"]:
        if current.count(BLOCK) != 1:
            fail("Managed-блок в product mk изменён или удалён. Автоматический откат остановлен.")
        # Preserve every user edit outside our exact managed block.
        restored_product = current.replace(BLOCK, b"", 1)
        print("Product mk изменён после установки: будут сохранены все изменения вне managed-блока.")
    else:
        restored_product = current
    if record["destination_existed"]:
        load_owned(backup / "payload-before")
    print(f"Отменить {args.transaction}: восстановить предыдущий пакет и убрать только добавленное подключение.")
    if args.dry_run:
        print("DRY RUN: файлы не изменены.")
        return
    removed = backup / "payload-rolled-back"
    if removed.exists():
        fail("Каталог сохранённого отката уже существует; требуется ручная проверка.")
    mode = stat.S_IMODE(product.stat().st_mode)
    destination.rename(removed)
    restored_payload = False
    try:
        if record["destination_existed"]:
            (backup / "payload-before").rename(destination)
            restored_payload = True
        write_atomic(product, restored_product, mode)
        record["status"] = "rolled-back"
        write_json(backup / "transaction.json", record)
    except BaseException:
        if restored_payload:
            destination.rename(backup / "payload-before")
        removed.rename(destination)
        write_atomic(product, current, mode)
        raise
    print("Откат завершён. Резервная копия и снятый пакет сохранены.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Подключение VELIQEN Link к исходникам ROM с резервной копией.")
    commands = parser.add_subparsers(dest="command", required=True)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--tree", required=True, help="Корень AOSP/LineageOS")
    install_parser.add_argument("--product-mk", required=True, help="Существующий makefile продукта, относительно дерева")
    install_parser.add_argument("--apk", help="APK с package tech.veliqen.link; по умолчанию из android/build-output")
    install_parser.add_argument("--dry-run", action="store_true")
    rollback_parser = commands.add_parser("rollback")
    rollback_parser.add_argument("--tree", required=True)
    rollback_parser.add_argument("--transaction", required=True)
    rollback_parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        (install if args.command == "install" else rollback)(args)
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, IndexError, struct.error, zipfile.BadZipFile) as error:
        print(f"ОСТАНОВЛЕНО: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
