"""Tests setup decisions and outgoing BlueZ requests without real devices."""
from pathlib import Path
import copy
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.bluetooth import BluetoothManager, BluetoothError, HFP_AG_UUID
from veliqen_link.audio import AudioRouter

ADAPTER = "/org/bluez/hci0"
PHONE = ADAPTER + "/dev_AA_BB_CC_DD_EE_FF"
OTHER = ADAPTER + "/dev_11_22_33_44_55_66"
ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakeBlueZ:
    def __init__(self, paired=True, powered=True):
        self.objects = {
            ADAPTER: {BluetoothManager.ADAPTER: {"Alias": "Ноутбук", "Powered": powered, "Discovering": False}},
            PHONE: {BluetoothManager.DEVICE: {
                "Address": ADDRESS, "Alias": "VELIQEN", "Paired": paired,
                "Connected": False, "Icon": "phone", "UUIDs": [HFP_AG_UUID], "Adapter": ADAPTER}},
        }
        self.calls = []
        self.pair_error = None
        self.profile_error = None
        self.finish_pair = True
        self.offline = False

    def call(self, path, interface, method, signature=None, args=(), timeout_ms=2500):
        self.calls.append((path, interface, method, signature, args))
        if self.offline:
            raise BluetoothError("offline")
        if method == "GetManagedObjects":
            return copy.deepcopy(self.objects)
        if method == "Pair":
            if self.pair_error:
                raise self.pair_error
            if self.finish_pair:
                self.objects[path][BluetoothManager.DEVICE]["Paired"] = True
        elif method == "ConnectProfile":
            if self.profile_error:
                raise self.profile_error
            self.objects[path][BluetoothManager.DEVICE]["Connected"] = True
        elif method == "StartDiscovery":
            self.objects[path][BluetoothManager.ADAPTER]["Discovering"] = True
        elif method == "StopDiscovery":
            self.objects[path][BluetoothManager.ADAPTER]["Discovering"] = False
        elif method == "CancelPairing":
            pass
        else:
            raise AssertionError("Unexpected or unsafe Bluetooth mutation: " + method)

    def set_powered(self, path):
        self.calls.append((path, BluetoothManager.ADAPTER, "SetPowered", None, (True,)))
        self.objects[path][BluetoothManager.ADAPTER]["Powered"] = True

    def mutations(self):
        return [c for c in self.calls if c[2] != "GetManagedObjects"]

    def add_phone(self, name="VELIQEN"):
        props = copy.deepcopy(self.objects[PHONE][BluetoothManager.DEVICE])
        props.update(Address="11:22:33:44:55:66", Alias=name)
        self.objects[OTHER] = {BluetoothManager.DEVICE: props}


class BluetoothSetupTests(unittest.TestCase):
    def setUp(self):
        self.bus = FakeBlueZ()
        self.launches = []
        self.manager = BluetoothManager(self.bus, which=lambda name: "/usr/bin/" + name if name == "gnome-control-center" else None,
                                        launcher=lambda args, **kwargs: self.launches.append(args))

    def test_snapshot_is_read_only_and_has_friendly_status(self):
        state = self.manager.snapshot()
        self.assertTrue(state["available"])
        self.assertTrue(state["powered"])
        self.assertEqual(state["phones"][0]["name"], "VELIQEN")
        self.assertEqual(state["state"], "available")
        self.assertFalse(self.bus.mutations())

    def test_off_and_unavailable_are_different_without_powering_hardware(self):
        self.bus.objects[ADAPTER][BluetoothManager.ADAPTER]["Powered"] = False
        self.assertEqual(self.manager.snapshot()["state"], "off")
        self.bus.offline = True
        self.assertFalse(self.manager.snapshot()["available"])
        self.assertFalse(self.bus.mutations())

    def test_remembered_phone_wins_over_same_name_and_never_other_address(self):
        self.bus.add_phone()
        choice = self.manager.suggest_phone(self.manager.snapshot(), "VELIQEN", ADDRESS)
        self.assertEqual(choice["device"]["address"], ADDRESS)
        self.assertFalse(choice["needs_confirmation"])
        self.assertEqual(choice["reason"], "remembered")

    def test_duplicate_names_are_not_automatically_bound(self):
        self.bus.add_phone()
        choice = self.manager.suggest_phone(self.manager.snapshot(), "veliqen")
        self.assertIsNone(choice["device"])
        self.assertTrue(choice["needs_confirmation"])
        self.assertEqual(len(choice["choices"]), 2)

    def test_name_match_and_single_different_phone_have_distinct_confirmation(self):
        choice = self.manager.suggest_phone(self.manager.snapshot(), "  VeLiQeN ")
        self.assertEqual(choice["reason"], "name")
        self.assertFalse(choice["needs_confirmation"])
        choice = self.manager.suggest_phone(self.manager.snapshot(), "Другой телефон")
        self.assertEqual(choice["reason"], "single_phone")
        self.assertTrue(choice["needs_confirmation"])

    def test_headphones_and_blocked_devices_are_not_phone_suggestions(self):
        props = self.bus.objects[PHONE][BluetoothManager.DEVICE]
        props.update(Icon="audio-headset", UUIDs=["0000111e-0000-1000-8000-00805f9b34fb"], Alias="Наушники")
        self.assertFalse(self.manager.snapshot()["phones"])
        self.assertIsNone(self.manager.suggest_phone(self.manager.snapshot(), "VELIQEN")["device"])
        self.assertIsNone(self.manager.suggest_phone(self.manager.snapshot(), "Наушники")["device"])
        self.assertEqual(self.manager.connect_phone(PHONE)["state"], "failed")
        self.assertFalse(self.bus.mutations())
        props.update(Icon="phone", UUIDs=[HFP_AG_UUID], Blocked=True)
        self.assertIsNone(self.manager.suggest_phone(self.manager.snapshot(), "Наушники", ADDRESS)["device"])

    def test_paired_device_connects_only_remote_call_profile(self):
        result = self.manager.connect_phone(PHONE)
        self.assertEqual(result["state"], "connected")
        self.assertEqual([(c[2], c[3], c[4]) for c in self.bus.mutations()], [("ConnectProfile", "(s)", (HFP_AG_UUID,))])
        self.assertFalse(self.launches)

    def test_pair_uses_default_system_agent_and_rechecks_paired_state(self):
        self.bus.objects[PHONE][BluetoothManager.DEVICE]["Paired"] = False
        result = self.manager.connect_phone(PHONE)
        self.assertEqual(result["state"], "connected")
        self.assertEqual([c[2] for c in self.bus.mutations()], ["Pair", "ConnectProfile"])
        # FakeBlueZ rejects every unlisted mutation: no RegisterAgent, Trusted,
        # RemoveDevice, automatic confirmation or generic profile connection.

    def test_pair_not_yet_confirmed_never_connects_profile(self):
        self.bus.objects[PHONE][BluetoothManager.DEVICE]["Paired"] = False
        self.bus.finish_pair = False
        result = self.manager.connect_phone(PHONE)
        self.assertEqual(result["state"], "pending")
        self.assertEqual([c[2] for c in self.bus.mutations()], ["Pair"])

    def test_rejected_pairing_does_not_retry_or_open_settings(self):
        self.bus.objects[PHONE][BluetoothManager.DEVICE]["Paired"] = False
        self.bus.pair_error = BluetoothError("rejected", "AuthenticationRejected")
        result = self.manager.connect_phone(PHONE)
        self.assertEqual(result["state"], "cancelled")
        self.assertEqual([c[2] for c in self.bus.mutations()], ["Pair"])
        self.assertFalse(self.launches)

    def test_missing_default_agent_opens_standard_settings(self):
        self.bus.objects[PHONE][BluetoothManager.DEVICE]["Paired"] = False
        self.bus.pair_error = BluetoothError("failed", "Failed", "No agent available")
        result = self.manager.connect_phone(PHONE)
        self.assertEqual(result["state"], "settings_opened")
        self.assertEqual(self.launches, [["/usr/bin/gnome-control-center", "bluetooth"]])
        self.assertEqual([c[2] for c in self.bus.mutations()], ["Pair"])

    def test_unavailable_call_profile_is_not_reported_as_ready(self):
        self.bus.profile_error = BluetoothError("not ready", "NotAvailable")
        result = self.manager.connect_phone(PHONE)
        self.assertEqual(result["state"], "needs_call_setup")
        self.assertIn("звонки", result["message"])

    def test_removed_or_injected_device_path_does_not_receive_commands(self):
        for path in ("/org/bluez/hci0/dev_11_22_33_44_55_66", PHONE + "/../../bad", "/"):
            self.assertEqual(self.manager.connect_phone(path)["state"], "failed")
        self.assertFalse(self.bus.mutations())

    def test_scan_releases_only_own_session_and_is_idempotent(self):
        self.bus.objects[ADAPTER][BluetoothManager.ADAPTER]["Discovering"] = True
        self.manager.stop_discovery()
        self.assertFalse(self.bus.mutations())
        self.manager.start_discovery()
        self.manager.start_discovery()
        self.manager.stop_discovery()
        self.manager.stop_discovery()
        self.assertEqual([c[2] for c in self.bus.mutations()], ["StartDiscovery", "StopDiscovery"])

    def test_explicit_power_on_does_not_change_discoverability_or_pairability(self):
        self.bus.objects[ADAPTER][BluetoothManager.ADAPTER]["Powered"] = False
        result = self.manager.power_on()
        self.assertEqual(result["state"], "powered")
        self.assertEqual([c[2] for c in self.bus.mutations()], ["SetPowered"])

    def test_cancel_does_not_cancel_another_app_pairing(self):
        self.assertFalse(self.manager.cancel_pairing(PHONE))
        self.assertFalse(self.bus.mutations())


class AudioDefaultSelectionTests(unittest.TestCase):
    def test_uses_actual_user_defaults_in_local_inventory(self):
        values = {"get-default-source": "usb-mic", "get-default-sink": "headphones"}
        router = AudioRouter(runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, values[args[-1]] + "\n", ""), which=lambda name: name)
        state = {"local_sources": [{"name": "built-in"}, {"name": "usb-mic"}],
                 "local_sinks": [{"name": "speakers"}, {"name": "headphones"}]}
        self.assertEqual(router.default_devices(state), {"source": "usb-mic", "sink": "headphones", "complete": True})

    def test_does_not_route_phone_default_back_into_phone_or_guess_multiple_outputs(self):
        router = AudioRouter(runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "phone-node\n", ""), which=lambda name: name)
        state = {"local_sources": [{"name": "built-in"}],
                 "local_sinks": [{"name": "speakers"}, {"name": "headphones"}]}
        self.assertEqual(router.default_devices(state), {"source": "built-in", "sink": None, "complete": False})

    def test_another_phone_gateway_is_not_eligible_as_automatic_local_audio(self):
        router = AudioRouter(runner=lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "other-phone\n", ""), which=lambda name: name)
        state = {"local_sources": [{"name": "built-in"}, {"name": "other-phone", "phone_gateway": True}],
                 "local_sinks": [{"name": "speakers"}, {"name": "other-phone", "phone_gateway": True}]}
        self.assertEqual(router.default_devices(state), {"source": "built-in", "sink": "speakers", "complete": True})


if __name__ == "__main__":
    unittest.main()
