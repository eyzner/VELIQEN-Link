"""Upgrade must stop only the verified user's application, without force kill."""
from pathlib import Path
import importlib.util
import signal
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / "scripts/STOP-RUNNING.py"
spec = importlib.util.spec_from_file_location("veliqen_upgrade_stop", path)
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)


class FakeBus:
    def __init__(self):
        self.name = ":1.40"
        self.user = 1000
        self.process = 24680
        self.reads = 0
        self.change_on_recheck = False

    def owner(self):
        self.reads += 1
        if self.change_on_recheck and self.reads == 2:
            self.name = ":1.41"
        return self.name

    def uid(self, owner):
        return self.user

    def pid(self, owner):
        return self.process


class FakeProcesses:
    def __init__(self, bus, exits=True):
        self.bus = bus
        self.exits = exits
        self.actions = []
        self.relaunch = False
        self.no_handle = False

    def open(self, pid, uid):
        self.actions.append(("open", pid, uid))
        return None if self.no_handle else object()

    def terminate(self, handle):
        self.actions.append(("signal", signal.SIGTERM))

    def wait_exit(self, handle, timeout):
        self.actions.append(("wait", timeout))
        if self.exits:
            self.bus.name = ":1.99" if self.relaunch else None
        return self.exits

    def close(self, handle):
        self.actions.append(("close",))


class UpgradeStopTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBus()
        self.process = FakeProcesses(self.bus)

    def run_stop(self, **kwargs):
        return stop.stop_running(self.bus, self.process, uid=1000, clock=lambda: 10, **kwargs)

    def test_no_owner_is_harmless_and_never_inspects_processes(self):
        self.bus.name = None
        self.assertEqual(self.run_stop()["state"], "absent")
        self.assertFalse(self.process.actions)

    def test_other_users_owner_cannot_receive_a_signal(self):
        self.bus.user = 1001
        with self.assertRaises(stop.StopError):
            self.run_stop()
        self.assertFalse(self.process.actions)

    def test_verified_owner_gets_sigterm_and_must_actually_exit(self):
        self.assertEqual(self.run_stop()["state"], "stopped")
        self.assertEqual(self.process.actions, [("open", 24680, 1000), ("signal", signal.SIGTERM), ("wait", 5.0), ("close",)])

    def test_timeout_is_an_error_with_no_force_kill_and_closed_handle(self):
        self.process.exits = False
        with self.assertRaises(stop.StopError) as error:
            self.run_stop(wait_seconds=60)
        self.assertIn("5 секунд", str(error.exception))
        self.assertEqual([action for action in self.process.actions if action[0] == "signal"], [("signal", signal.SIGTERM)])
        self.assertEqual([action for action in self.process.actions if action[0] == "wait"], [("wait", 5.0)])
        self.assertEqual(self.process.actions[-1], ("close",))

    def test_changed_owner_is_not_terminated(self):
        self.bus.change_on_recheck = True
        with self.assertRaises(stop.StopError):
            self.run_stop()
        self.assertFalse(any(action[0] == "signal" for action in self.process.actions))
        self.assertEqual(self.process.actions[-1], ("close",))

    def test_relaunched_application_blocks_upgrade_without_second_signal(self):
        self.process.relaunch = True
        with self.assertRaises(stop.StopError):
            self.run_stop()
        self.assertEqual([a for a in self.process.actions if a[0] == "signal"], [("signal", signal.SIGTERM)])

    def test_missing_handle_does_not_claim_success_while_name_is_still_owned(self):
        self.process.no_handle = True
        with self.assertRaises(stop.StopError):
            self.run_stop()
        self.assertFalse(any(action[0] == "signal" for action in self.process.actions))

    def test_production_signal_is_sigterm_on_pidfd_only(self):
        handle = stop.ProcessHandle(24680, 7, 123)
        with patch.object(stop.signal, "pidfd_send_signal") as send, patch.object(stop.os, "kill") as numeric_kill:
            stop.Processes.terminate(handle)
        send.assert_called_once_with(7, signal.SIGTERM)
        numeric_kill.assert_not_called()

    def test_only_expected_application_commands_are_eligible(self):
        self.assertTrue(stop.Processes._is_link_command(["/usr/bin/python3", "-m", "veliqen_link", "--background"]))
        self.assertFalse(stop.Processes._is_link_command(["/usr/bin/python3", "unrelated.py"]))
        self.assertFalse(stop.Processes._is_link_command(["/bin/sh", "-c", "veliqen_link"]))

    def test_no_session_or_missing_gi_is_a_harmless_first_install(self):
        with patch.object(stop, "SessionBus", side_effect=stop.NoSession("Нет прежнего сеанса")), patch("builtins.print"):
            self.assertEqual(stop.main([]), 0)


if __name__ == "__main__":
    unittest.main()
