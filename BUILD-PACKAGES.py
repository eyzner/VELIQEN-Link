#!/usr/bin/env python3
"""Build the standalone Ubuntu amd64 DEB and GNOME extension ZIP.

No package script invokes apt, changes a user's home, or starts a GUI. APT
resolves Depends. Per-user permissions and optional setup stay in the app.
"""
from pathlib import Path
import gzip
import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = "1.2.1"
ARCH = "amd64"
OUTPUT = ROOT / "Установочные-файлы"
OUTPUT.mkdir(exist_ok=True)
VENDOR = ROOT / "vendor/scrcpy-linux-x86_64-v5.0.1.tar.gz"
VENDOR_SHA256 = "9f969d30cc574816077edecda65719c1c70b0aee1df1ba5fa00779ac07b8fd6e"
# ELF DT_NEEDED + SDL display backends, plus the external tools called by VELIQEN.
DEPENDENCIES = [
    "python3 (>= 3.10)", "python3-gi", "python3-gi-cairo", "gir1.2-gtk-3.0",
    "gir1.2-ayatanaappindicator3-0.1", "desktop-file-utils", "kdeconnect", "bluez",
    "pipewire (>= 1.4)", "pipewire-bin (>= 1.4)", "pipewire-pulse (>= 1.4)",
    "wireplumber (>= 0.5)", "libspa-0.2-bluetooth (>= 1.4)", "pulseaudio-utils",
    "pkexec", "android-sdk-platform-tools-common", "libc6 (>= 2.35)",
    "libgcc-s1", "libudev1", "libegl1", "libgl1", "libx11-6", "libxext6",
    "libxfixes3", "libxi6", "libxrandr2", "libxss1", "libwayland-client0",
    "libwayland-cursor0", "libwayland-egl1", "libxkbcommon0",
]
EPOCH = int(os.environ.get("SOURCE_DATE_EPOCH", "1795219200"))
if hashlib.sha256(VENDOR.read_bytes()).hexdigest() != VENDOR_SHA256:
    raise SystemExit("The official scrcpy archive checksum does not match.")


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


with tempfile.TemporaryDirectory(prefix="veliqen-deb-") as temporary:
    stage = Path(temporary)
    stage.chmod(0o755)
    control = stage / "DEBIAN"
    control.mkdir()
    app = stage / "usr/share/veliqen-link"
    app.mkdir(parents=True)
    shutil.copytree(ROOT / "linux/veliqen_link", app / "veliqen_link",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ["ENABLE-CALLS.sh", "ALLOW-SCREEN.py"]:
        copy(ROOT / "scripts" / name, app / "scripts" / name)
    for source, destination in [
        ("linux/veliqen-link", "usr/bin/veliqen-link"),
        ("linux/veliqen-link.desktop", "usr/share/applications/veliqen-link.desktop"),
        ("linux/veliqen-link.svg", "usr/share/icons/hicolor/scalable/apps/veliqen-link.svg"),
        ("linux/veliqen-link-autostart.desktop", "etc/xdg/autostart/veliqen-link.desktop"),
    ]:
        copy(ROOT / source, stage / destination)
    extension = ROOT / "gnome-extension/veliqen-clipboard@veliqen.tech"
    shutil.copytree(extension, stage / "usr/share/gnome-shell/extensions" / extension.name)
    vendor = stage / "usr/lib/veliqen-link/scrcpy"
    vendor.mkdir(parents=True)
    with tarfile.open(VENDOR) as archive:
        # Fixed file allowlist, verified archive, no archive-provided paths or links.
        for name in ["scrcpy", "adb", "scrcpy-server", "LICENSE", "scrcpy.1", "scrcpy.png", "disconnected.png"]:
            member = archive.getmember("scrcpy-linux-x86_64-v5.0.1/" + name)
            if not member.isfile():
                raise SystemExit("Unexpected scrcpy archive member: " + name)
            with archive.extractfile(member) as source:
                (vendor / name).write_bytes(source.read())
    docs = stage / "usr/share/doc/veliqen-link"
    docs.mkdir(parents=True)
    for name in ["README_RU.md", "README.md", "RELEASE-NOTES.md", "LICENSE", "THIRD_PARTY.md"]:
        if (ROOT / name).is_file():
            copy(ROOT / name, docs / name)
    copy(ROOT / "vendor/SCRCPY_PROVENANCE.json", docs / "SCRCPY_PROVENANCE.json")
    if (ROOT / "vendor/SOURCES.md").is_file():
        copy(ROOT / "vendor/SOURCES.md", docs / "SOURCES.md")
    copy(vendor / "LICENSE", docs / "scrcpy-LICENSE")
    if (ROOT / "vendor/licenses").is_dir():
        shutil.copytree(ROOT / "vendor/licenses", docs / "vendor-licenses")
    # Keep user docs and screenshots; large internal test/build logs stay in source.
    shutil.copytree(ROOT / "docs", docs / "docs", ignore=shutil.ignore_patterns("qa", "development"))
    (docs / "copyright").write_text("VELIQEN Link desktop integration: GPL-3.0-or-later.\n"
        "See LICENSE and THIRD_PARTY.md for authorship and component sources.\n"
        "The unmodified scrcpy distribution is bundled under its upstream terms.\n"
        "See scrcpy-LICENSE and SCRCPY_PROVENANCE.json.\n")
    changelog = f"veliqen-link ({VERSION}) stable; urgency=medium\n\n  * Standalone DEB with bundled scrcpy, system GNOME bridge and session autostart.\n  * Declare audio tools, version requirements and native runtime dependencies.\n  * Keep per-user setup and permissions in the application.\n\n -- VELIQEN Project  Sat, 21 Nov 2026 00:00:00 +0000\n"
    (docs / "changelog.gz").write_bytes(gzip.compress(changelog.encode(), mtime=0))
    (control / "conffiles").write_text("/etc/xdg/autostart/veliqen-link.desktop\n")
    executable = {stage / "usr/bin/veliqen-link", vendor / "scrcpy", vendor / "adb"}
    for path in stage.rglob("*"):
        path.chmod(0o755 if path.is_dir() or path in executable else 0o644)
    installed_size = sum((p.stat().st_size + 1023) // 1024 for p in stage.rglob("*")
                         if p.is_file() and control not in p.parents)
    (control / "control").write_text(f"""Package: veliqen-link
Version: {VERSION}
Section: net
Priority: optional
Architecture: {ARCH}
Maintainer: VELIQEN Project
Installed-Size: {installed_size}
Depends: {", ".join(DEPENDENCIES)}
Description: Local phone integration for Ubuntu and VELIQEN OS
 Native GTK interface for KDE Connect data exchange, PipeWire HFP calls
 and consent-based screen sharing. Includes scrcpy, a GNOME clipboard
 extension and desktop autostart. No cloud account is required.
""")
    (control / "md5sums").write_text("".join(
        hashlib.md5(p.read_bytes()).hexdigest() + "  " + p.relative_to(stage).as_posix() + "\n"
        for p in sorted(stage.rglob("*")) if p.is_file() and control not in p.parents
        and not p.relative_to(stage).as_posix().startswith("etc/")))
    for path in control.iterdir():
        path.chmod(0o644)
    for path in [stage, *stage.rglob("*")]:
        os.utime(path, (EPOCH, EPOCH))
    package = OUTPUT / f"veliqen-link_{VERSION}_{ARCH}.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--threads-max=1", "-Zxz", "-z6", "--build", str(stage), str(package)],
        check=True, env=dict(os.environ, SOURCE_DATE_EPOCH=str(EPOCH)))
    (package.with_suffix(package.suffix + ".sha256")).write_text(
        hashlib.sha256(package.read_bytes()).hexdigest() + "  " + package.name + "\n")

with zipfile.ZipFile(OUTPUT / "veliqen-clipboard@veliqen.tech.zip", "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(extension.iterdir()):
        if path.is_file():
            info = zipfile.ZipInfo(path.name, (2026, 11, 21, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())
print("Standalone package:", package)
