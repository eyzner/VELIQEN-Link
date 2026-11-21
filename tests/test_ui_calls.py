"""Regressions for call actions: no duplicate dial or unrelated audio teardown."""
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.app import LinkApplication

class UiCallTests(unittest.TestCase):
    def harness(self):
        h = SimpleNamespace(dial_pending=False, dial_button=Mock(),
                            dial_number=Mock(), telephony=Mock(), audio=Mock(),
                            refresh=Mock(), say=Mock(), _refresh_audio=Mock())
        h.dial_number.get_text.return_value = "+381600000001"
        h._gateway = lambda: {"path": "/gateway"}
        h.jobs = []
        h.work = lambda operation, callback=None, message=None, **kwargs: h.jobs.append((operation, callback, kwargs))
        return h

    def test_rapid_double_click_never_creates_a_second_dial(self):
        h = self.harness()
        with patch("veliqen_link.app.GLib.timeout_add_seconds") as timeout:
            LinkApplication._dial(h)
            LinkApplication._dial(h)
            self.assertEqual(len(h.jobs), 1)
            operation, callback, _ = h.jobs.pop()
            callback(operation())
            LinkApplication._dial(h)
            self.assertEqual(h.jobs, [])
            h.telephony.dial.assert_called_once_with("/gateway", "+381600000001")
            self.assertTrue(h.dial_pending)
            # Re-enable only after the deliberate post-response cooldown.
            timeout.call_args.args[1]()
            self.assertFalse(h.dial_pending)

    def test_declining_another_phone_does_not_blindly_stop_current_audio(self):
        h = self.harness()
        state = {"calls": [{"address": "AA:BB:CC:DD:EE:FF", "state": "active"}]}
        h.telephony.refresh.return_value = state
        LinkApplication._hangup(h, "/other-phone/incoming")
        operation, callback, _ = h.jobs.pop()
        callback(operation())
        h.telephony.hangup.assert_called_once_with("/other-phone/incoming")
        h.audio.stop.assert_not_called()
        h.audio.reconcile_calls.assert_called_once_with(state)

    def test_partial_audio_cleanup_is_not_reported_as_success(self):
        h = self.harness()
        h.audio.stop.return_value = {"active": True, "errors": ["busy"], "status": "Маршрут занят"}
        LinkApplication._stop_audio(h)
        operation, callback, _ = h.jobs.pop()
        callback(operation())
        h.say.assert_called_once_with("Маршрут занят", error=True)

    def test_missing_adb_error_reaches_the_screen_page(self):
        h = self.harness()
        h.adb_selector = Mock()
        h.screen_status = Mock()
        h.screen = Mock()
        LinkApplication._refresh_adb(h)
        _, _, callbacks = h.jobs.pop()
        callbacks["on_error"]("Не найдена программа adb")
        h.screen_status.set_text.assert_called_once_with("Не найдена программа adb")

if __name__ == "__main__":
    unittest.main()
