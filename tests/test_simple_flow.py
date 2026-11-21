"""User-flow regressions for the simplified desktop, without contacting a phone."""
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.app import LinkApplication
from veliqen_link.screenlink import SCREEN_REQUEST


class SimpleFlowTests(unittest.TestCase):
    def request_harness(self):
        h = SimpleNamespace(current_device="phone_a", native_session=None, native_viewer=None,
            native_request_pending=False, native_request_device=None, native_request_sequence=0,
            native_screen_button=Mock(), native_screen_status=Mock(), receiver=Mock(), kde=Mock(),
            select_page=Mock(), say=Mock(), _pair_dialog=Mock(), jobs=[])
        h._device = lambda: h.current_device
        h.receiver.start.return_value = {"listening": True}
        h.work = lambda operation, callback=None, **kwargs: h.jobs.append((operation, callback, kwargs))
        return h

    def test_screen_request_uses_existing_pair_and_does_not_start_capture_itself(self):
        h = self.request_harness()
        with patch("veliqen_link.app.GLib.timeout_add_seconds"):
            LinkApplication._request_native_screen(h)
        operation, callback, _ = h.jobs[0]
        callback(operation())
        h.kde.ping.assert_called_once_with("phone_a", SCREEN_REQUEST)
        h.receiver.set_allowed_devices.assert_not_called()
        self.assertIsNone(h.native_session)
        self.assertIsNone(h.native_viewer)

    def test_double_click_sends_only_one_request(self):
        h = self.request_harness()
        with patch("veliqen_link.app.GLib.timeout_add_seconds"):
            LinkApplication._request_native_screen(h)
            LinkApplication._request_native_screen(h)
        self.assertEqual(len(h.jobs), 1)

    def test_changed_selection_cancels_a_queued_request(self):
        h = self.request_harness()
        with patch("veliqen_link.app.GLib.timeout_add_seconds"):
            LinkApplication._request_native_screen(h)
        h.current_device = "phone_b"
        h.jobs[0][0]()
        h.kde.ping.assert_not_called()
        h.receiver.set_allowed_devices.assert_not_called()

    def test_old_timeout_cannot_clear_a_new_request(self):
        h = self.request_harness()
        with patch("veliqen_link.app.GLib.timeout_add_seconds") as timeout:
            LinkApplication._request_native_screen(h)
        expired = timeout.call_args.args[1]
        h.native_request_sequence += 1
        h.native_screen_button.reset_mock()
        expired()
        self.assertTrue(h.native_request_pending)
        h.native_screen_button.set_sensitive.assert_not_called()

    def stream_harness(self):
        return SimpleNamespace(closing=False, current_device="phone_b", native_generation=3,
            native_session={"device_id": "phone_b", "session_id": "B", "control": True},
            native_viewer=Mock(), receiver=Mock(), native_frame_lock=threading.Lock(),
            native_frame=None, native_frame_scheduled=False, native_request_pending=False,
            native_request_sequence=1, native_screen_status=Mock(), native_screen_button=Mock(),
            screen_input_pool=Mock(), say=Mock())

    def test_old_end_does_not_close_new_phone_session(self):
        h = self.stream_harness()
        LinkApplication._native_session_ended(h, "old finished", "A")
        self.assertEqual(h.native_session["session_id"], "B")
        h.native_viewer.ended.assert_not_called()

    def test_old_begin_can_only_stop_its_own_session(self):
        h = self.stream_harness()
        LinkApplication._native_session_started(h, {"device_id": "phone_a", "session_id": "A"})
        h.receiver.stop_session.assert_called_once_with(session_id="A")
        self.assertEqual(h.native_session["session_id"], "B")

    def test_old_frame_is_never_drawn_on_a_new_phone_session(self):
        h = self.stream_harness()
        h.native_frame = ("A", b"old pixels")
        h.native_frame_scheduled = True
        LinkApplication._draw_native_frame(h)
        h.native_viewer.frame.assert_not_called()
        self.assertFalse(h.native_frame_scheduled)

    def test_frames_coalesce_to_the_latest_for_the_same_session(self):
        h = self.stream_harness()
        h._draw_native_frame = lambda: LinkApplication._draw_native_frame(h)
        with patch("veliqen_link.app.GLib.idle_add") as idle:
            LinkApplication._native_frame_received(h, b"one", "B")
            LinkApplication._native_frame_received(h, b"two", "B")
        self.assertEqual(idle.call_count, 1)
        LinkApplication._draw_native_frame(h)
        h.native_viewer.frame.assert_called_once_with(b"two")

    def test_queued_input_cannot_cross_to_a_new_session(self):
        h = self.stream_harness()
        LinkApplication._send_screen_command(h, {"op": "home"})
        pending = h.screen_input_pool.submit.call_args.args[0]
        h.native_generation += 1
        h.native_session = {"device_id": "phone_b", "session_id": "C", "control": True}
        pending()
        h.receiver.send_command.assert_not_called()

    def test_input_is_bound_to_the_captured_session_id(self):
        h = self.stream_harness()
        LinkApplication._send_screen_command(h, {"op": "home"})
        h.screen_input_pool.submit.call_args.args[0]()
        h.receiver.send_command.assert_called_once_with({"op": "home"}, session_id="B")

    def test_a_different_bluetooth_phone_cannot_be_used_for_dialing(self):
        h = SimpleNamespace(current_device="phone_a", config={"bluetooth_devices": {"phone_a": "AA:AA:AA:AA:AA:AA"}},
            gateway_selector=Mock(), say=Mock(), calls_state={"gateways": [
                {"path": "/gateway_b", "address": "BB:BB:BB:BB:BB:BB"}]})
        h.gateway_selector.get_active_id.return_value = "/gateway_b"
        self.assertIsNone(LinkApplication._gateway(h))

    def test_confirmed_bluetooth_binding_can_be_used(self):
        gateway = {"path": "/gateway_a", "address": "AA:AA:AA:AA:AA:AA"}
        h = SimpleNamespace(current_device="phone_a", config={"bluetooth_devices": {"phone_a": gateway["address"]}},
            gateway_selector=Mock(), say=Mock(), calls_state={"gateways": [gateway]})
        h.gateway_selector.get_active_id.return_value = "/gateway_a"
        self.assertEqual(LinkApplication._gateway(h), gateway)


if __name__ == "__main__":
    unittest.main()
