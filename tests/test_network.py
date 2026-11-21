"""Firewall rules use only a paired phone IP and fixed commands; no root runs."""
from pathlib import Path
import importlib.util
import json
import stat
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "linux"))
from veliqen_link import network
spec = importlib.util.spec_from_file_location("veliqen_allow_screen", ROOT / "scripts/ALLOW-SCREEN.py")
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


class FakeUfw:
    def __init__(self, status="Status: active\n", returncode=0):
        self.status, self.returncode, self.calls = status, returncode, []

    def __call__(self, args, **kwargs):
        self.calls.append((args, kwargs))
        if args == [helper.UFW, "status"]:
            return subprocess.CompletedProcess(args, self.returncode, self.status, "")
        expected = [helper.UFW, "allow", "from", "192.168.1.7", "to", "any", "port", "38475", "proto", "tcp", "comment", "VELIQEN Link Screen"]
        if args != expected:
            raise AssertionError("Unexpected privileged command")
        return subprocess.CompletedProcess(args, 0, "Rule added\n", "")


class PrivilegedRuleTests(unittest.TestCase):
    def test_only_rfc1918_host_addresses_are_accepted(self):
        for address in ("10.1.2.3", "172.16.0.9", "172.31.255.7", "192.168.1.7"):
            self.assertEqual(helper.validate_address(address), address)
        for address in ("192.168.1.0/24", "192.168.1.7:38475", "192.168.1.7; reboot", "192.168.1.7\n", "127.0.0.1", "100.64.1.7", "8.8.8.8", "::ffff:192.168.1.7", "172.32.0.1", "169.254.1.7"):
            with self.subTest(address=address), self.assertRaises(helper.RuleError):
                helper.validate_address(address)

    def test_non_root_cannot_even_query_ufw(self):
        run = FakeUfw()
        with self.assertRaises(helper.RuleError):
            helper.apply_rule("192.168.1.7", runner=run, euid=1000, exists=lambda _: True)
        self.assertFalse(run.calls)

    def test_active_ufw_gets_only_one_exact_phone_and_fixed_tcp_port(self):
        run = FakeUfw()
        result = helper.apply_rule("192.168.1.7", runner=run, euid=0, exists=lambda _: True)
        self.assertEqual(result["state"], "allowed")
        self.assertEqual(len(run.calls), 2)
        self.assertEqual(run.calls[0][1]["env"]["LC_ALL"], "C")
        self.assertFalse(any("enable" in args or "disable" in args for args, _ in run.calls))
        self.assertTrue(all("shell" not in kwargs for _, kwargs in run.calls))

    def test_inactive_ufw_is_not_enabled_or_modified(self):
        run = FakeUfw("Status: inactive\n")
        result = helper.apply_rule("192.168.1.7", runner=run, euid=0, exists=lambda _: True)
        self.assertEqual(result["state"], "inactive")
        self.assertEqual([args for args, _ in run.calls], [[helper.UFW, "status"]])

    def test_missing_ufw_has_no_side_effect(self):
        run = FakeUfw()
        result = helper.apply_rule("192.168.1.7", runner=run, euid=0, exists=lambda _: False)
        self.assertEqual(result["state"], "not_installed")
        self.assertFalse(run.calls)

    def test_failed_or_unknown_status_is_not_treated_as_active(self):
        for run in (FakeUfw("unexpected\n"), FakeUfw("Status: active\n", returncode=1)):
            with self.assertRaises(helper.RuleError):
                helper.apply_rule("192.168.1.7", runner=run, euid=0, exists=lambda _: True)
            self.assertEqual(len(run.calls), 1)


class AddressSelectionTests(unittest.TestCase):
    def phone(self, addresses, **kwargs):
        return {"paired": True, "reachable": True, "addresses": addresses, **kwargs}

    def test_uses_bare_verified_lan_address_ignores_mac_and_ipv6(self):
        device = self.phone(["AA:BB:CC:DD:EE:FF", "fe80::1234%wlan0", "192.168.1.7", "::ffff:192.168.1.7"])
        self.assertEqual(network.screen_address(device), "192.168.1.7")

    def test_does_not_extract_an_address_from_urls_or_arbitrary_text(self):
        for value in ("http://192.168.1.7/", "192.168.1.7 (LAN)", "192.168.1.7:1716", "192.168.1.7;shutdown"):
            with self.assertRaises(network.NetworkError):
                network.screen_address(self.phone([value]))

    def test_requires_current_pairing_and_reachability(self):
        for overrides in ({"paired": False}, {"reachable": False}, {"paired": "true"}):
            with self.assertRaises(network.NetworkError):
                network.screen_address(self.phone(["192.168.1.7"], **overrides))

    def test_ambiguous_or_public_addresses_are_not_silently_selected(self):
        for values in (["192.168.1.7", "10.1.2.3"], ["8.8.8.8"], []):
            with self.assertRaises(network.NetworkError):
                network.screen_address(self.phone(values))


class UserBackendTests(unittest.TestCase):
    def test_invalid_address_never_opens_authentication(self):
        with patch.object(network, "_installed_helper_is_trusted") as verify:
            with self.assertRaises(network.NetworkError):
                network.open_screen_port("192.168.1.7;echo bad")
            verify.assert_not_called()

    def test_only_installed_trusted_helper_is_executed_without_shell(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            return subprocess.CompletedProcess(args, 0, json.dumps({"state": "allowed", "message": "Готово", "address": "192.168.1.7"}), "")
        with patch.object(network, "_installed_helper_is_trusted", return_value=True), patch.object(network.os, "access", return_value=True):
            self.assertEqual(network.open_screen_port("192.168.1.7", runner=run)["state"], "allowed")
        self.assertEqual(calls[0][0], ["/usr/bin/pkexec", "/usr/bin/python3", "-I", "/usr/share/veliqen-link/scripts/ALLOW-SCREEN.py", "--address", "192.168.1.7"])
        self.assertNotIn("shell", calls[0][1])

    def test_source_or_untrusted_helper_never_gets_elevated(self):
        with patch.object(network, "_installed_helper_is_trusted", return_value=False), patch.object(network.subprocess, "run") as run:
            self.assertEqual(network.open_screen_port("192.168.1.7")["state"], "missing_helper")
            run.assert_not_called()

    def test_user_cancellation_never_retries(self):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 126, "", "cancelled")
        with patch.object(network, "_installed_helper_is_trusted", return_value=True), patch.object(network.os, "access", return_value=True):
            self.assertEqual(network.open_screen_port("192.168.1.7", runner=run)["state"], "cancelled")
        self.assertEqual(len(calls), 1)

    def test_ownership_check_rejects_user_writable_ancestors_and_symlinks(self):
        def good(path):
            return SimpleNamespace(st_uid=0, st_mode=(stat.S_IFREG | 0o644) if path == network.INSTALLED_HELPER else (stat.S_IFDIR | 0o755))
        with patch.object(Path, "lstat", autospec=True, side_effect=good):
            self.assertTrue(network._installed_helper_is_trusted())
        for bad_mode, bad_uid in ((stat.S_IFDIR | 0o777, 0), (stat.S_IFDIR | 0o755, 1000), (stat.S_IFLNK | 0o777, 0)):
            def bad(path, mode=bad_mode, uid=bad_uid):
                if path == network.INSTALLED_HELPER.parent:
                    return SimpleNamespace(st_uid=uid, st_mode=mode)
                return good(path)
            with patch.object(Path, "lstat", autospec=True, side_effect=bad):
                self.assertFalse(network._installed_helper_is_trusted())


if __name__ == "__main__":
    unittest.main()
