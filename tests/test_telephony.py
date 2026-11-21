import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.telephony import TelephonyManager, TelephonyError, normalize_number

ADDRESS = "AA:BB:CC:DD:EE:FF"
ROOT = TelephonyManager.ROOT
AG = ROOT + "/ag0"
CALL = AG + "/call0"


def xml(interfaces):
    return "<node>" + "".join('<interface name="' + name + '">' + "".join('<method name="' + m + '"/>' for m in methods) + "</interface>" for name, methods in interfaces.items()) + "</node>"


class FakeSystem:
    kind = "system"

    def __init__(self, paired=True, connected=True):
        self.paired, self.connected = paired, connected
        self.actions = []

    def call(self, service, path, interface, method, signature=None, args=()):
        if service == "org.bluez" and method == "GetManagedObjects":
            return {"/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF": {"org.bluez.Device1": {
                "Address": ADDRESS, "Alias": "My phone", "Paired": self.paired, "Connected": self.connected}}}
        if method == "NameHasOwner":
            return False
        if service == "org.bluez" and method == "Connect":
            self.actions.append((method, args))
            return None
        raise RuntimeError((service, path, interface, method))


class FakeSession:
    kind = "session"

    def __init__(self, compatibility=False):
        self.compatibility = compatibility
        self.state = "incoming"
        self.number = "+381600000001"
        self.actions = []
        self.removed = False
        self.methods = {"Dial", "SendTones"}

    def call(self, service, path, interface, method, signature=None, args=()):
        if method == "NameHasOwner":
            return True
        if method == "Introspect":
            if path == ROOT:
                return xml({"org.ofono.Manager": {"GetModems"}} if self.compatibility else {TelephonyManager.OM: {"GetManagedObjects"}})
            if path == AG:
                return xml({TelephonyManager.OLD_GATEWAY if self.compatibility else TelephonyManager.GATEWAY:
                            self.methods | ({"GetCalls"} if self.compatibility else set())})
            if path == CALL:
                return xml({TelephonyManager.OLD_CALL if self.compatibility else TelephonyManager.CALL: {"Answer", "Hangup"}})
        call_props = {"State": self.state, "LineIdentification": self.number}
        if method == "GetManagedObjects":
            data = {AG: {TelephonyManager.GATEWAY: {"Address": ADDRESS}}}
            if not self.removed:
                data[CALL] = {TelephonyManager.CALL: call_props}
            return data
        if method == "GetModems":
            return [(AG, {"Address": ADDRESS})]
        if method == "GetCalls":
            return [] if self.removed else [(CALL, call_props)]
        if method in ("Answer", "Hangup", "Dial", "SendTones"):
            self.actions.append((method, args))
            return None
        raise RuntimeError((service, path, interface, method))


class TelephonyTests(unittest.TestCase):
    def setUp(self):
        self.session, self.system = FakeSession(), FakeSystem()
        self.phone = TelephonyManager(self.session, self.system)

    def test_refresh_never_places_or_answers_calls(self):
        state = self.phone.refresh()
        self.assertTrue(state["available"])
        self.assertEqual(state["gateways"][0]["address"], ADDRESS)
        self.assertEqual(state["calls"][0]["state"], "incoming")
        self.assertEqual(self.session.actions, [])

    def test_native_and_compatibility_interfaces(self):
        for compatibility in (False, True):
            session = FakeSession(compatibility)
            phone = TelephonyManager(session, self.system)
            session.state = "disconnected"
            phone.dial(AG, "+381 (60) 123-456")
            session.state = "incoming"
            phone.answer(CALL)
            phone.send_tones(AG, "12#")
            phone.hangup(CALL)
            self.assertEqual(session.actions, [("Dial", ("+38160123456",)), ("Answer", ()), ("SendTones", ("12#",)), ("Hangup", ())])

    def test_unpaired_or_disconnected_phone_cannot_be_used(self):
        for attr in ("paired", "connected"):
            system = FakeSystem()
            setattr(system, attr, False)
            phone = TelephonyManager(self.session, system)
            self.assertFalse(phone.refresh()["available"])
            with self.assertRaises(TelephonyError):
                phone.dial(AG, "+38160123")
        self.assertEqual(self.session.actions, [])

    def test_stale_call_is_not_sent_to_new_object(self):
        self.phone.refresh()
        self.session.removed = True
        with self.assertRaises(TelephonyError):
            self.phone.answer(CALL)
        self.assertEqual(self.session.actions, [])

    def test_answer_only_incoming_not_active_or_waiting(self):
        for state in ("active", "waiting", "disconnected"):
            self.session.state = state
            with self.assertRaises(TelephonyError):
                self.phone.answer(CALL)
        self.assertEqual(self.session.actions, [])

    def test_only_advertised_methods_are_called(self):
        self.session.methods = {"Dial"}
        with self.assertRaises(TelephonyError):
            self.phone.send_tones(AG, "1")
        self.assertEqual(self.session.actions, [])

    def test_input_validation_blocks_control_characters(self):
        for number in ("12\nATD999;", "123;reboot", "", "1" * 81, "$(touch /tmp/x)"):
            with self.assertRaises(TelephonyError):
                normalize_number(number)
        self.assertEqual(normalize_number("+7 (999) 123-45-67"), "+79991234567")

    def test_bluetooth_connect_requires_existing_pair(self):
        self.system.paired = False
        with self.assertRaises(TelephonyError):
            self.phone.connect_bluetooth(ADDRESS)
        self.assertEqual(self.system.actions, [])

    def test_dial_cannot_start_a_second_call_in_any_live_state(self):
        for state in ("incoming", "active", "held", "dialing", "alerting", "waiting"):
            self.session.state = state
            with self.subTest(state=state), self.assertRaises(TelephonyError):
                self.phone.dial(AG, "+381600000002")
        self.assertEqual(self.session.actions, [])

    def test_call_tones_use_the_exact_active_phone_on_both_interfaces(self):
        for compatibility in (False, True):
            session = FakeSession(compatibility)
            session.state = "active"
            phone = TelephonyManager(session, self.system)
            phone.send_call_tones(CALL, "12*#", expected_address=ADDRESS, expected_number=session.number)
            self.assertEqual(session.actions, [("SendTones", ("12*#",))])

    def test_call_tones_refuse_a_held_ended_or_replaced_call(self):
        self.session.state = "active"
        self.phone.refresh()
        for state in ("incoming", "held", "disconnected"):
            self.session.state = state
            with self.subTest(state=state), self.assertRaises(TelephonyError):
                self.phone.send_call_tones(CALL, "1", expected_address=ADDRESS, expected_number=self.session.number)
        self.session.state = "active"
        with self.assertRaises(TelephonyError):
            self.phone.send_call_tones(CALL, "1", expected_address=ADDRESS, expected_number="+381600000002")
        with self.assertRaises(TelephonyError):
            self.phone.send_call_tones(CALL, "1", expected_address="11:22:33:44:55:66", expected_number=self.session.number)
        self.session.removed = True
        with self.assertRaises(TelephonyError):
            self.phone.send_call_tones(CALL, "1", expected_address=ADDRESS, expected_number=self.session.number)
        self.assertEqual(self.session.actions, [])

    def test_call_tones_refuse_an_unpaired_phone_and_unsupported_method(self):
        self.session.state = "active"
        self.system.paired = False
        with self.assertRaises(TelephonyError):
            self.phone.send_call_tones(CALL, "1", expected_address=ADDRESS, expected_number=self.session.number)
        self.system.paired = True
        self.session.methods = {"Dial"}
        with self.assertRaises(TelephonyError):
            self.phone.send_call_tones(CALL, "1", expected_address=ADDRESS, expected_number=self.session.number)
        self.assertEqual(self.session.actions, [])

    def test_call_tones_reject_multiple_active_calls_on_one_gateway(self):
        class TwoCalls(FakeSession):
            def call(self, service, path, interface, method, signature=None, args=()):
                if method == "Introspect" and path == CALL + "_second":
                    return xml({TelephonyManager.CALL: {"Answer", "Hangup"}})
                result = super().call(service, path, interface, method, signature, args)
                if method == "GetManagedObjects":
                    result[CALL + "_second"] = {TelephonyManager.CALL: {"State": "active", "LineIdentification": "+381600000003"}}
                return result
        session = TwoCalls()
        session.state = "active"
        phone = TelephonyManager(session, self.system)
        with self.assertRaises(TelephonyError):
            phone.send_call_tones(CALL, "1", expected_address=ADDRESS, expected_number=session.number)
        self.assertEqual(session.actions, [])


if __name__ == "__main__":
    unittest.main()
