#!/usr/bin/env python3
"""Verify DEB payload and isolated dpkg lifecycle without installing on the host.

The dpkg lifecycle uses a fresh temporary root inside an unprivileged user
namespace. Dependencies are deliberately absent and ignored only inside this
fixture. This checks the package lifecycle, not an Ubuntu dependency download
or a live GNOME/Bluetooth/phone session.
"""
import argparse
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--package", type=Path,
    default=ROOT / "Установочные-файлы/veliqen-link_1.2.1_amd64.deb")
parser.add_argument("--previous", type=Path)
parser.add_argument("--inside-user-namespace", action="store_true")
args = parser.parse_args()
package = args.package.resolve()

if not args.inside_user_namespace:
    command = ["unshare", "-Ur", sys.executable, str(Path(__file__).resolve()),
        "--inside-user-namespace", "--package", str(package)]
    if args.previous:
        command += ["--previous", str(args.previous.resolve())]
    raise SystemExit(subprocess.run(command, check=False).returncode)
if os.geteuid() != 0:
    raise SystemExit("Expected mapped root in the unprivileged user namespace")


def output(command, **kwargs):
    result = subprocess.run(command, text=True, capture_output=True, check=False, **kwargs)
    if result.returncode:
        raise AssertionError(f"Command failed ({result.returncode}): {command}\n{result.stdout}\n{result.stderr}")
    return result.stdout


control = output(["dpkg-deb", "-f", str(package)])
assert "Architecture: amd64\n" in control
assert "Version: 1.2.1\n" in control
for dependency in ["python3-gi-cairo", "desktop-file-utils", "pipewire-bin (>= 1.4)", "wireplumber (>= 0.5)",
                   "pkexec", "libudev1", "gir1.2-ayatanaappindicator3-0.1"]:
    assert dependency in control, dependency
with tempfile.TemporaryDirectory(prefix="veliqen-control-") as temporary:
    output(["dpkg-deb", "-e", str(package), temporary])
    files = {p.name for p in Path(temporary).iterdir()}
    assert files == {"control", "conffiles", "md5sums"}, files
print("PASS control: amd64, version/dependency constraints, no maintainer scripts")

payload = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
    members = archive.getmembers()
    names = {member.name.removeprefix("./") for member in members}
    for member in members:
        assert member.uid == 0 and member.gid == 0, member.name
        assert not member.mode & 0o6022, member.name
        assert member.isdir() or member.isfile(), member.name
        if member.isdir():
            assert member.mode == 0o755, (member.name, oct(member.mode))
        assert not member.name.startswith("/") and ".." not in Path(member.name).parts
    for expected in ["usr/bin/veliqen-link", "usr/lib/veliqen-link/scrcpy/scrcpy",
                     "usr/lib/veliqen-link/scrcpy/adb", "usr/lib/veliqen-link/scrcpy/scrcpy-server",
                     "usr/share/gnome-shell/extensions/veliqen-clipboard@veliqen.tech/extension.js",
                     "usr/share/applications/veliqen-link.desktop",
                     "etc/xdg/autostart/veliqen-link.desktop"]:
        assert expected in names, expected
    for source in (ROOT / "vendor/licenses").iterdir():
        if source.is_file():
            member_name = "./usr/share/doc/veliqen-link/vendor-licenses/" + source.name
            assert archive.extractfile(member_name).read() == source.read_bytes(), source.name
    assert "usr/share/doc/veliqen-link/SOURCES.md" in names
print("PASS payload: root ownership, safe modes, launcher, scrcpy/ADB/server, GNOME bridge, XDG autostart")
print("PASS notices: every vendor license/NOTICE retained byte for byte, corresponding-source guide included")


def lifecycle(previous=None):
    with tempfile.TemporaryDirectory(prefix="veliqen-isolated-dpkg-") as temporary:
        root = Path(temporary)
        admin = root / "var/lib/dpkg"
        admin.mkdir(parents=True)
        (admin / "status").write_text("")
        (root / "var/log").mkdir()
        user_settings = root / "home/fixture/.config/veliqen-link/settings.json"
        user_settings.parent.mkdir(parents=True)
        original = b'{"device_id":"fixture-known-pair","clipboard_sync":false}\n'
        user_settings.write_bytes(original)
        command = ["dpkg", "--root=" + str(root), "--log=" + str(root / "var/log/dpkg.log"),
                   "--force-depends", "--no-triggers"]
        if previous:
            output([*command, "--install", str(previous)])
        output([*command, "--install", str(package)])
        status = (admin / "status").read_text()
        assert "Status: install ok installed" in status and "Version: 1.2.1" in status
        assert user_settings.read_bytes() == original
        app = root / "usr/share/veliqen-link"
        version = output(["/usr/bin/python3", "-m", "veliqen_link", "--version"],
                         env=dict(os.environ, PYTHONPATH=str(app)))
        assert version.strip() == "1.2.1"
        for path in (ROOT / "linux/veliqen_link").glob("*.py"):
            assert (app / "veliqen_link" / path.name).read_bytes() == path.read_bytes(), path.name
        output(["desktop-file-validate", str(root / "usr/share/applications/veliqen-link.desktop"),
                str(root / "etc/xdg/autostart/veliqen-link.desktop")])
        for binary in ("scrcpy", "adb"):
            assert os.access(root / "usr/lib/veliqen-link/scrcpy" / binary, os.X_OK)
        auto = root / "etc/xdg/autostart/veliqen-link.desktop"
        auto.write_text(auto.read_text() + "\n# local admin preference\n")
        preserved = auto.read_bytes()
        output([*command, "--force-confold", "--install", str(package)])
        assert auto.read_bytes() == preserved
        output([*command, "--remove", "veliqen-link"])
        assert auto.read_bytes() == preserved
        assert not (root / "usr/bin/veliqen-link").exists()
        output([*command, "--purge", "veliqen-link"])
        assert not auto.exists()
        assert user_settings.read_bytes() == original


lifecycle()
print("PASS isolated dpkg: fresh install, module --version, exact source bytes, reinstall, remove, purge")
if args.previous:
    lifecycle(args.previous.resolve())
    print("PASS isolated dpkg: upgrade from previous all package to amd64, user settings preserved")
print("SCOPE: temporary roots only; dependency installation and live Ubuntu/phone services not exercised")
