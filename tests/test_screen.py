import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.screen import ScreenManager, ScreenError, validate_endpoint, parse_devices


class FakeProcess:
    pid = 1234
    stdout = []

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class ScreenTests(unittest.TestCase):
    def setUp(self):
        self.commands, self.spawned = [], []
        self.authorized = True
        self.pair_error = False
        self.manager = ScreenManager(runner=self.run_command, popen=self.spawn, which=lambda n: "/usr/bin/" + n, bundled_dirs=())

    def run_command(self, args, **kwargs):
        self.commands.append((args, kwargs))
        if args[1:3] == ["devices", "-l"]:
            output = "List of devices attached\nmy-phone " + ("device" if self.authorized else "unauthorized") + " model:VELIQEN_Phone\nsecond-phone device model:Other\n"
        elif args[1] == "pair":
            if self.pair_error:
                return subprocess.CompletedProcess(args, 1, "echoed secret " + kwargs["input"], "failure")
            output = "Successfully paired to " + args[2]
        elif args[1] == "connect":
            output = "connected to " + args[2]
        elif args[1] == "--help":
            output = "--no-audio --turn-screen-off --serial --max-size"
        else:
            output = ""
        return subprocess.CompletedProcess(args, 0, output, "")

    def spawn(self, args, **kwargs):
        self.spawned.append((args, kwargs))
        return FakeProcess()

    def test_pairing_code_only_stdin_never_arguments(self):
        self.manager.pair("192.168.1.3:37001", "123456")
        args, kwargs = self.commands[-1]
        self.assertNotIn("123456", " ".join(args))
        self.assertEqual(kwargs["input"], "123456\n")
        self.assertNotIn("shell", kwargs)

    def test_pair_failure_does_not_echo_code(self):
        self.pair_error = True
        with self.assertRaises(ScreenError) as caught:
            self.manager.pair("192.168.1.3:37001", "123456")
        self.assertNotIn("123456", str(caught.exception))

    def test_host_port_validation_rejects_shell_and_options(self):
        for endpoint in ("-connect:1234", "192.168.1.3;touch /x:1234", "host:0", "host:65536", "999.1.1.1:12", "http://host:12", "host:22\nfoo"):
            with self.assertRaises(ScreenError):
                validate_endpoint(endpoint)
        for endpoint in ("192.168.1.3:12345", "phone.local:12345", "[fd00::1]:12345"):
            self.assertEqual(validate_endpoint(endpoint), endpoint)

    def test_launch_uses_selected_authorized_serial(self):
        self.manager.launch("my-phone", screen_off=True)
        args, kwargs = self.spawned[0]
        self.assertIn("--serial=my-phone", args)
        self.assertIn("--no-audio", args)
        self.assertIn("--turn-screen-off", args)
        self.assertNotIn("shell", kwargs)
        self.assertFalse(any("tcpip" in args or "shell" in args for args, _ in self.commands))

    def test_unauthorized_serial_never_launches_scrcpy(self):
        self.authorized = False
        with self.assertRaises(ScreenError):
            self.manager.launch("my-phone")
        self.assertEqual(self.spawned, [])

    def test_missing_serial_never_falls_back_to_another_phone(self):
        with self.assertRaises(ScreenError):
            self.manager.launch("missing-phone")
        self.assertEqual(self.spawned, [])

    def test_parse_device_states(self):
        data = parse_devices("List of devices attached\na unauthorized\nb offline\nc device model:T60 transport_id:3\n")
        self.assertEqual([d["authorized"] for d in data], [False, False, True])
        self.assertEqual(data[-1]["model"], "T60")

    def test_bundled_tools_and_server_are_selected_without_global_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            for name in ("scrcpy", "adb"):
                path = directory / name
                path.write_text("#!/bin/sh\nexit 0\n")
                path.chmod(0o755)
            (directory / "scrcpy-server").write_bytes(b"fake test server")
            manager = ScreenManager(runner=self.run_command, popen=self.spawn,
                                    which=lambda n: "/usr/bin/" + n, bundled_dirs=(directory,))
            manager.launch("my-phone")
            args, kwargs = self.spawned[-1]
            self.assertEqual(args[0], str(directory / "scrcpy"))
            self.assertEqual(kwargs["env"]["ADB"], str(directory / "adb"))
            self.assertEqual(kwargs["env"]["SCRCPY_SERVER_PATH"], str(directory / "scrcpy-server"))


if __name__ == "__main__":
    unittest.main()
