#!/usr/bin/env python3
"""Exercise real installer safety on a temporary source tree and a real APK.
Run: python3 test_integrator.py --apk ../android/build-output/VELIQEN-Link-1.2.1-unsigned.apk
SPDX-License-Identifier: GPL-3.0-or-later
"""
from pathlib import Path
import argparse
import hashlib
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent


def snapshot(tree):
    return {str(p.relative_to(tree)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tree.rglob("*") if p.is_file()}


def run(arguments, success=True):
    result = subprocess.run([sys.executable, str(HERE / "integrate.py"), *arguments], capture_output=True, text=True)
    if (result.returncode == 0) != success:
        raise AssertionError(result.stdout + result.stderr)
    return result.stdout + result.stderr


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apk", required=True)
    args = parser.parse_args()
    apk = str(Path(args.apk).resolve())
    with tempfile.TemporaryDirectory(prefix="veliqen-rom-check-") as temporary:
        tree = Path(temporary)
        (tree / "build").mkdir()
        (tree / "build/envsetup.sh").write_text("# Test source tree; never a connected device\n")
        product = tree / "product.mk"
        original = b"# My existing product\nPRODUCT_NAME := test_product\n"
        product.write_bytes(original)
        install = ["install", "--tree", str(tree), "--product-mk", "product.mk", "--apk", apk]

        before = snapshot(tree)
        run([*install, "--dry-run"])
        assert snapshot(tree) == before, "Dry run wrote files"
        run(install)
        installed = snapshot(tree)
        output = run(install)
        assert "Изменений нет" in output and snapshot(tree) == installed, "Repeated install is not idempotent"
        transactions = list((tree / ".veliqen-link-backups").iterdir())
        assert len(transactions) == 1, "Idempotent install created another backup"
        transaction = transactions[0].name
        rollback = ["rollback", "--tree", str(tree), "--transaction", transaction]

        # User edits inside payload must prevent destructive rollback and update.
        edited = tree / "vendor/veliqen/link/product.mk"
        previous_payload = edited.read_bytes()
        edited.write_bytes(previous_payload + b"# User modification\n")
        changed = snapshot(tree)
        run(install, success=False)
        run(rollback, success=False)
        assert snapshot(tree) == changed, "Conflict handling changed user data"
        edited.write_bytes(previous_payload)

        # Unrelated later edits to the product must survive removing our block.
        product.write_bytes(product.read_bytes() + b"PRODUCT_PACKAGES += MyLaterApp\n")
        before_rollback = snapshot(tree)
        run([*rollback, "--dry-run"])
        assert snapshot(tree) == before_rollback, "Rollback dry run wrote files"
        run(rollback)
        assert not (tree / "vendor/veliqen/link").exists(), "Payload remained after rollback"
        assert b"PRODUCT_PACKAGES += MyLaterApp\n" in product.read_bytes(), "Unrelated edit was lost"
        assert b"BEGIN VELIQEN LINK" not in product.read_bytes(), "Managed block remained"
        rolled_back = snapshot(tree)
        run(rollback)
        assert snapshot(tree) == rolled_back, "Repeated rollback changed files"

        # A fresh install/rollback restores the makefile exactly when untouched.
        product.write_bytes(original)
        run(install)
        latest = max((tree / ".veliqen-link-backups").iterdir(), key=lambda p: p.stat().st_mtime_ns)
        run(["rollback", "--tree", str(tree), "--transaction", latest.name])
        assert product.read_bytes() == original, "Exact original file was not restored"
    print("PASS: dry run, APK package validation, idempotence, conflict protection, preservation of later edits, exact rollback.")


if __name__ == "__main__":
    main()
