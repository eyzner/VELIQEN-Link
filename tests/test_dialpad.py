"""Keypad transitions and real application callbacks, without phone hardware."""
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.app import LinkApplication
from veliqen_link.dialpad import Dialpad, active_call, call_key
from veliqen_link.telephony import TelephonyError
from gi.repository import Gdk, Gtk

ADDRESS = "AA:BB:CC:DD:EE:FF"
GATEWAY = {"path": "/phone", "address": ADDRESS}
CALL = {"path": "/phone/call1", "gateway_path": "/phone", "address": ADDRESS,
        "number": "+381600000001", "state": "active"}


class KeypadCallbackTests(unittest.TestCase):
    def harness(self, calls=None):
        bindings = {"phone": ADDRESS, "other": "11:22:33:44:55:66"}
        h = SimpleNamespace(current_device="phone", config=Mock(), telephony=Mock(), say=Mock(),
                            refresh=Mock(), select_page=Mock(), dial_pending=False,
                            calls_state={"gateways": [dict(GATEWAY)], "calls": list(calls or [])},
                            gateway_selector=Mock(), dial_number=Mock(), dial_button=Mock(),
                            _tone_queue=[], _tone_pending=False, jobs=[])
        h.config.get.side_effect = lambda name: bindings if name == "bluetooth_devices" else None
        h.gateway_selector.get_active_id.return_value = GATEWAY["path"]
        h.dial_number.get_text.return_value = "+381600000002"
        h.work = lambda operation, callback=None, message=None, **kwargs: h.jobs.append((operation, callback, kwargs))
        for name in ("_gateway", "_dial", "_send_tone", "_drain_tones", "_update_dialpad", "_pick_number"):
            setattr(h, name, MethodType(getattr(LinkApplication, name), h))
        return h

    def test_digits_are_serialized_for_the_captured_call_not_new_dials(self):
        h = self.harness([dict(CALL)])
        for digit in "12#":
            self.assertTrue(h._send_tone(digit, call_key(CALL)))
        self.assertEqual(len(h.jobs), 1)
        for digit in "12#":
            operation, done, _ = h.jobs.pop(0)
            done(operation())
            h.telephony.send_call_tones.assert_called_with(CALL["path"], digit,
                                                          expected_address=ADDRESS,
                                                          expected_number=CALL["number"])
        self.assertEqual(h.jobs, [])
        self.assertEqual(h._tone_queue, [])
        h.telephony.dial.assert_not_called()

    def test_phone_switch_before_worker_execution_cancels_queued_digits(self):
        h = self.harness([dict(CALL)])
        h._send_tone("1", call_key(CALL))
        h._send_tone("2", call_key(CALL))
        h.current_device = "other"
        operation, _, handlers = h.jobs.pop()
        with self.assertRaises(TelephonyError) as failure:
            operation()
        handlers["on_error"](str(failure.exception))
        self.assertFalse(h._tone_pending)
        self.assertEqual(h._tone_queue, [])
        self.assertEqual(h.jobs, [])
        h.telephony.send_call_tones.assert_not_called()

    def test_stale_keypad_never_sends_to_a_replacement_call(self):
        for change in ({"path": "/phone/call2"}, {"number": "+381600000003"}, {"state": "held"}):
            h = self.harness([{**CALL, **change}])
            self.assertFalse(h._send_tone("1", call_key(CALL)))
            self.assertEqual(h.jobs, [])

    def test_any_live_selected_call_blocks_outgoing_dial(self):
        for state in ("active", "incoming", "held", "waiting", "dialing", "alerting"):
            h = self.harness([{**CALL, "state": state}])
            h._dial()
            self.assertEqual(h.jobs, [], state)
            h.telephony.dial.assert_not_called()

    def test_phone_switch_before_dial_worker_does_not_call_old_phone(self):
        h = self.harness()
        h._dial()
        h.current_device = "other"
        operation, _, handlers = h.jobs.pop()
        with self.assertRaises(TelephonyError) as failure:
            operation()
        handlers["on_error"](str(failure.exception))
        self.assertFalse(h.dial_pending)
        h.telephony.dial.assert_not_called()

    def test_wrong_gateway_cannot_bypass_saved_bluetooth_binding(self):
        h = self.harness([dict(CALL)])
        h.current_device = "other"
        self.assertIsNone(h._gateway())
        self.assertFalse(h._send_tone("1", call_key(CALL)))
        h._dial()
        self.assertEqual(h.jobs, [])

    def test_multiple_active_calls_are_ambiguous_but_other_phone_is_ignored(self):
        self.assertIsNone(active_call([CALL, {**CALL, "path": "/phone/call2"}], GATEWAY))
        other = {**CALL, "path": "/other/call", "gateway_path": "/other", "address": "11:22:33:44:55:66"}
        self.assertEqual(active_call([CALL, other], GATEWAY), CALL)

    def test_picking_a_number_opens_keypad_without_placing_a_call(self):
        h = self.harness()
        h.dialpad = Mock(call_key=None)
        h.dialpad.set_number.return_value = True
        self.assertTrue(h._pick_number("+12345"))
        h.select_page.assert_called_once_with("calls")
        h.dialpad.set_number.assert_called_once_with("+12345")
        self.assertEqual(h.jobs, [])
        h.telephony.dial.assert_not_called()

    def test_render_mode_changes_discard_only_unsent_tones(self):
        h = self.harness([dict(CALL)])
        h.dialpad = SimpleNamespace(call_key=call_key(CALL), set_state=Mock())
        h._tone_queue.append(("phone", CALL, "5"))
        h.calls_state["calls"] = []
        h._update_dialpad()
        self.assertEqual(h._tone_queue, [])
        h.dialpad.set_state.assert_called_once_with("dial", ready=True, pending=False, call_key=None)


@unittest.skipUnless(Gtk.init_check()[0], "GTK display is unavailable")
class KeypadGtkTests(unittest.TestCase):
    def setUp(self):
        self.on_call = Mock()
        self.on_tone = Mock(return_value=True)
        self.pad = Dialpad(self.on_call, self.on_tone)
        self.window = Gtk.Window()
        self.window.add(self.pad)
        self.window.show_all()
        self.pad.set_state("dial", ready=True)

    def tearDown(self):
        self.window.destroy()

    def test_buttons_plus_backspace_and_enter_edit_then_call_once(self):
        self.pad.plus_button.emit("clicked")
        for digit in "0123456789*#":
            self.pad.keys[digit].emit("clicked")
        self.assertEqual(self.pad.entry.get_text(), "+0123456789*#")
        self.pad.erase_button.emit("clicked")
        self.assertEqual(self.pad.entry.get_text(), "+0123456789*")
        self.pad.entry.emit("activate")
        self.on_call.assert_called_once_with()
        self.on_tone.assert_not_called()

    def test_in_call_keyboard_sends_tone_and_enter_never_calls(self):
        self.pad.set_number("+12345")
        self.pad.set_state("tones", ready=True, call_key=call_key(CALL))
        for keyval in (Gdk.KEY_1, Gdk.KEY_KP_2, Gdk.KEY_numbersign):
            self.assertTrue(self.pad._key_pressed(self.pad.entry, SimpleNamespace(keyval=keyval, state=0)))
        self.pad._key_pressed(self.pad.entry, SimpleNamespace(keyval=Gdk.KEY_Return, state=0))
        self.pad.entry.emit("activate")
        self.pad.plus_button.emit("clicked")
        self.assertEqual([c.args[0] for c in self.on_tone.call_args_list], ["1", "2", "#"])
        self.assertEqual(self.pad.entry.get_text(), "12#")
        self.on_call.assert_not_called()
        self.pad.set_state("dial", ready=True)
        self.assertEqual(self.pad.entry.get_text(), "+12345")

    def test_busy_or_blocked_keypad_cannot_start_another_call(self):
        self.pad.set_number("12345")
        self.pad.set_state("dial", ready=True, pending=True)
        self.pad.entry.emit("activate")
        self.pad.call_button.emit("clicked")
        self.pad.set_state("blocked", ready=True)
        self.pad.keys["1"].emit("clicked")
        self.pad.entry.emit("activate")
        self.assertEqual(self.pad.entry.get_text(), "12345")
        self.on_call.assert_not_called()
        self.on_tone.assert_not_called()

    def test_rejected_tone_does_not_appear_as_sent_and_new_call_clears_digits(self):
        self.pad.set_state("tones", ready=True, call_key=call_key(CALL))
        self.on_tone.return_value = False
        self.pad.keys["1"].emit("clicked")
        self.assertEqual(self.pad.entry.get_text(), "")
        self.on_tone.return_value = True
        self.pad.keys["2"].emit("clicked")
        self.pad.set_state("tones", ready=True, call_key=call_key({**CALL, "path": "/phone/call2"}))
        self.assertEqual(self.pad.entry.get_text(), "")


if __name__ == "__main__":
    unittest.main()
