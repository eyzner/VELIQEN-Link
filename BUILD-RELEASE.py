#!/usr/bin/env python3
"""Assemble public source/full/ROM ZIPs and release assets; never publish.

Build DEB and signed APK first. Signing credentials are intentionally outside
this project. Deterministic ZIP metadata and per-archive SHA256 manifests help
inspect the actual payload, without claiming reproducible Android compilation.
"""
from pathlib import Path
import argparse
import hashlib
import os
import shutil
import stat
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = (ROOT / "VERSION").read_text().strip()
STAMP = (2026, 11, 21, 0, 0, 0)
DEBUG_KEY = "android/kdeconnect-android/debug.keystore"
DEBUG_KEY_SHA256 = "02dd883e3014f4c7f3af2468a25b776ec43fecfba6759bc10cb7ea6694fc14e7"
SKIP_PARTS = {".git", ".gradle", ".kotlin", ".cxx", ".idea", "__pycache__", "node_modules", "build", "dist", "private-signing"}
SKIP_NAMES = {"local.properties", ".DS_Store", "MANIFEST-SHA256.txt"}
SECRET_NAMES = {"store-password", "key-password", "keystore.properties", "secrets.json", ".env"}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_files(source_only=False):
    selected = {}
    for path in sorted(ROOT.rglob("*")):
        relative = path.relative_to(ROOT)
        name = relative.as_posix()
        if any(part in SKIP_PARTS or part.startswith("qa-") for part in relative.parts):
            continue
        if path.name in SKIP_NAMES or path.suffix in {".pyc", ".idsig", ".log"}:
            continue
        if path.is_symlink():
            raise SystemExit("Refusing a symlink in the release: " + name)
        if not path.is_file():
            continue
        if path.name in SECRET_NAMES or path.name.startswith(".env.") or path.suffix.lower() in {".p12", ".pfx", ".jks", ".key", ".keystore"}:
            if name != DEBUG_KEY or digest(path) != DEBUG_KEY_SHA256:
                raise SystemExit("Refusing signing credentials in the public tree: " + name)
        if name.startswith("android/artifacts/"):
            # Keep text evidence, not duplicate APKs. Full kit has installable
            # APK in artifacts and platform prebuilt in rom/payload/prebuilt.
            if path.suffix not in {".txt", ".json"}:
                continue
        if source_only and (path.suffix in {".apk", ".aab", ".deb"} or name.startswith("artifacts/")):
            continue
        if source_only and name.startswith("presentations/"):
            continue
        selected[name] = path
    return selected


def write_zip(destination, prefix, files):
    manifest = "".join(f"{digest(path)}  {name}\n" for name, path in files.items())
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name, path in files.items():
            info = zipfile.ZipInfo(prefix + "/" + name, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
            info.external_attr = (stat.S_IFREG | mode) << 16
            archive.writestr(info, path.read_bytes())
        info = zipfile.ZipInfo(prefix + "/MANIFEST-SHA256.txt", STAMP)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100644 << 16
        archive.writestr(info, manifest.encode())
    with zipfile.ZipFile(destination) as archive:
        bad = archive.testzip()
        if bad:
            raise SystemExit("ZIP integrity failure: " + str(bad))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    output = args.output.resolve()
    if output != ROOT / "dist" and ROOT in output.parents:
        raise SystemExit("Use dist/ or an output directory outside the source tree.")
    deb = ROOT / f"artifacts/veliqen-link_{VERSION}_amd64.deb"
    apk = ROOT / f"artifacts/VELIQEN-Link-{VERSION}-Android.apk"
    rom_apk = ROOT / "rom/payload/prebuilt/VELIQENLink.apk"
    for path in (deb, apk, rom_apk):
        if not path.is_file():
            raise SystemExit("Required built artifact is missing: " + str(path))
    output.mkdir(parents=True, exist_ok=True)
    delivered = []
    for path in [deb, apk, *sorted((ROOT / "presentations").glob("*.pptx")),
                 *sorted((ROOT / "presentations").glob("*.pdf")), ROOT / "RELEASE-NOTES.md"]:
        target = output / path.name
        shutil.copyfile(path, target)
        target.chmod(0o644)
        delivered.append(target)
    source = output / f"VELIQEN-Link-{VERSION}-Source.zip"
    write_zip(source, f"VELIQEN-Link-{VERSION}", select_files(source_only=True))
    delivered.append(source)
    rom = output / f"VELIQEN-Link-{VERSION}-ROM.zip"
    files = {name: path for name, path in select_files().items() if name.startswith("rom/")}
    write_zip(rom, f"VELIQEN-Link-{VERSION}-ROM", files)
    delivered.append(rom)
    full = output / f"VELIQEN-Link-{VERSION}-Full.zip"
    write_zip(full, f"VELIQEN-Link-{VERSION}", select_files())
    delivered.append(full)
    sums = output / "SHA256SUMS.txt"
    sums.write_text("".join(f"{digest(path)}  {path.name}\n" for path in sorted(delivered)))
    for path in [*delivered, sums]:
        print(f"{path.name}\t{path.stat().st_size}\t{digest(path)}")


if __name__ == "__main__":
    main()
