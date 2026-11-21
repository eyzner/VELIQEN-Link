import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))
from veliqen_link import session_setup as setup
from veliqen_link.screen import ScreenManager


class Settings:
    def __init__(self, blocked=False, writable=True):
        self.values = {"enabled-extensions": ["existing@example"],
                       "disabled-extensions": ["other@example", setup.UUID],
                       "disable-user-extensions": blocked}
        self.writable = writable

    def get_boolean(self, name): return self.values[name]
    def is_writable(self, name): return self.writable
    def get_strv(self, name): return list(self.values[name])
    def set_strv(self, name, value):
        self.values[name] = list(value)
        return True


class SessionSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.data = self.base / "data"
        self.system = self.base / "system" / setup.UUID
        self.system.mkdir(parents=True)
        (self.system / "extension.js").write_text("system version")
        self.user = self.data / "gnome-shell/extensions" / setup.UUID
        self.uid = patch.object(os, "geteuid", return_value=1000)
        self.uid.start()
        self.addCleanup(self.uid.stop)

    def install_legacy(self):
        shutil.copytree(ROOT / "gnome-extension" / setup.UUID, self.user)

    def test_autostart_default_and_user_opt_out(self):
        config = self.base / "config"
        self.assertTrue(setup.autostart_enabled(config))
        setup.set_autostart(False, config)
        self.assertFalse(setup.autostart_enabled(config))
        self.assertEqual(setup.autostart_path(config).stat().st_mode & 0o777, 0o600)
        setup.set_autostart(True, config)
        self.assertTrue(setup.autostart_enabled(config))

    def test_autostart_keeps_unrelated_files_and_refuses_symlink(self):
        config = self.base / "config"
        target = self.base / "unrelated"
        target.write_text("keep exactly")
        path = setup.autostart_path(config)
        path.parent.mkdir(parents=True)
        path.symlink_to(target)
        with self.assertRaises(RuntimeError): setup.set_autostart(False, config)
        self.assertEqual(target.read_text(), "keep exactly")
        self.assertTrue(path.is_symlink())

    def test_existing_external_disable_is_respected(self):
        config = self.base / "config"
        path = setup.autostart_path(config)
        path.parent.mkdir(parents=True)
        path.write_text("[Desktop Entry]\nX-GNOME-Autostart-enabled=false\n")
        self.assertFalse(setup.autostart_enabled(config))

    def test_root_never_writes_user_session(self):
        with patch.object(os, "geteuid", return_value=0):
            with self.assertRaises(RuntimeError): setup.set_autostart(True, self.base)
            with self.assertRaises(RuntimeError): setup.enable_clipboard_extension(settings=Settings())
        self.assertFalse((self.base / "autostart").exists())

    def test_exact_legacy_override_moves_to_backup_idempotently(self):
        self.install_legacy()
        before = {p.name: p.read_bytes() for p in self.user.iterdir()}
        self.assertEqual(setup.migrate_legacy_extension(self.data, self.system), "migrated")
        self.assertFalse(self.user.exists())
        backup = next((self.data / "veliqen-link/backups").glob("*/" + setup.UUID))
        self.assertEqual({p.name: p.read_bytes() for p in backup.iterdir()}, before)
        self.assertEqual(setup.migrate_legacy_extension(self.data, self.system), "none")

    def test_modified_override_is_preserved(self):
        self.install_legacy()
        source = self.user / "extension.js"
        source.write_text(source.read_text() + "\n// local change")
        before = source.read_bytes()
        self.assertEqual(setup.migrate_legacy_extension(self.data, self.system), "custom")
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.data / "veliqen-link/backups").exists())

    def test_extra_files_or_symlinks_are_preserved(self):
        self.install_legacy()
        (self.user / "my-custom-file").write_text("keep")
        self.assertEqual(setup.migrate_legacy_extension(self.data, self.system), "custom")
        (self.user / "my-custom-file").unlink()
        outside = self.base / "outside"
        outside.write_bytes((self.user / "extension.js").read_bytes())
        (self.user / "extension.js").unlink()
        (self.user / "extension.js").symlink_to(outside)
        self.assertEqual(setup.migrate_legacy_extension(self.data, self.system), "custom")
        self.assertTrue((self.user / "extension.js").is_symlink())

    def test_enable_only_our_extension_and_save_next_login(self):
        settings = Settings()
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 1, "", "not yet known")
        message = setup.enable_clipboard_extension(settings=settings, runner=runner,
            data_home=self.data, system_extension=self.system)
        self.assertEqual(settings.values["enabled-extensions"], ["existing@example", setup.UUID])
        self.assertEqual(settings.values["disabled-extensions"], ["other@example"])
        self.assertFalse(settings.values["disable-user-extensions"])
        self.assertIn("следующего входа", message)
        self.assertEqual(commands, [["gnome-extensions", "enable", setup.UUID]])

    def test_global_disable_and_admin_lock_are_not_changed(self):
        for settings in (Settings(blocked=True), Settings(writable=False)):
            before = dict(settings.values)
            with self.assertRaises(RuntimeError):
                setup.enable_clipboard_extension(settings=settings,
                    runner=lambda *_a, **_kw: self.fail("must not invoke shell"),
                    data_home=self.data, system_extension=self.system)
            self.assertEqual(settings.values, before)

    def test_custom_override_does_not_enable_or_replace_it(self):
        self.install_legacy()
        (self.user / "extension.js").write_text("user modification")
        settings = Settings()
        before = dict(settings.values)
        with self.assertRaises(RuntimeError):
            setup.enable_clipboard_extension(settings=settings, runner=lambda *_a, **_kw: None,
                data_home=self.data, system_extension=self.system)
        self.assertEqual(settings.values, before)
        self.assertEqual((self.user / "extension.js").read_text(), "user modification")

    def test_package_bundle_precedes_old_user_bundle(self):
        manager = ScreenManager()
        self.assertEqual(manager._bundled_dirs[0], Path("/usr/lib/veliqen-link/scrcpy"))
        with patch.object(Path, "is_file", return_value=True), patch.object(os, "access", return_value=True):
            self.assertEqual(manager._find_tool("scrcpy"), "/usr/lib/veliqen-link/scrcpy/scrcpy")


if __name__ == "__main__": unittest.main()
