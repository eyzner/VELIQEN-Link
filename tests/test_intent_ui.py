"""Actual intent/draft callbacks with cancellable dialogs and stateful fields."""
from pathlib import Path
import sys
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.app import LinkApplication
from veliqen_link.intents import parse_intent
from gi.repository import Gtk


class Field:
    def __init__(self, text=""):
        self.text = text
        self.grab_focus = Mock()

    def get_text(self, *_args):
        return self.text

    def set_text(self, text):
        self.text = text

    def get_start_iter(self):
        return 0

    def get_end_iter(self):
        return len(self.text)


class Rows:
    def __init__(self, rows):
        self.rows = list(rows)
        self.selected = self.rows[0]
        self.on_select = None

    def get_children(self):
        return list(self.rows)

    def remove(self, row):
        self.rows.remove(row)

    def select_row(self, row):
        self.selected = row
        if self.on_select:
            self.on_select(self, row)

    def unselect_all(self):
        self.select_row(None)


class IntentCallbackTests(unittest.TestCase):
    def harness(self, draft="Сохранить мой черновик"):
        body, history = Field(draft), Field("Существующая переписка")
        old = SimpleNamespace(thread={"thread_id": 1, "addresses": ["+111"]})
        new = SimpleNamespace(thread={"thread_id": 2, "addresses": ["+222"]})
        h = SimpleNamespace(sms_number=Field("+111"), sms_sim=Field("2"),
                            sms_body=SimpleNamespace(get_buffer=lambda: body),
                            message_view=SimpleNamespace(get_buffer=lambda: history, show_messages=Mock()),
                            threads=Rows([old, new]), selected_thread=1,
                            current_device="phone_a", window=None, jobs=[],
                            select_page=Mock(), _pick_number=Mock(), _request_native_screen=Mock(),
                            _send_sms=Mock(), _dial=Mock(), kde=Mock(), telephony=Mock(),
                            _save=Mock(), _render_devices=Mock(), _render_calls=Mock(), say=Mock())
        h._device = lambda: h.current_device
        h.work = lambda operation, callback=None, **kwargs: h.jobs.append((operation, callback, kwargs))
        for name in ("_handle_intent", "_confirm_new_message", "_new_message", "_thread_selected", "_select_device", "_device_changed"):
            setattr(h, name, MethodType(getattr(LinkApplication, name), h))
        h.threads.on_select = h._thread_selected
        return h

    def assert_no_transmission(self, h):
        h._send_sms.assert_not_called()
        h._dial.assert_not_called()
        h.kde.send_sms.assert_not_called()
        h.telephony.dial.assert_not_called()
        self.assertEqual(h.jobs, [])

    def assert_draft_untouched(self, h):
        self.assertEqual(h.sms_number.text, "+111")
        self.assertEqual(h.sms_sim.text, "2")
        self.assertEqual(h.sms_body.get_buffer().text, "Сохранить мой черновик")
        self.assertEqual(h.message_view.get_buffer().text, "Существующая переписка")
        self.assertEqual(h.selected_thread, 1)

    def test_tel_intent_only_prefills_keypad_and_leaves_sms_draft_alone(self):
        h = self.harness()
        with patch("veliqen_link.app.Gtk.MessageDialog") as dialog:
            h._handle_intent(parse_intent("tel:+7(999)123-45-67"))
        h._pick_number.assert_called_once_with("+79991234567")
        dialog.assert_not_called()
        self.assert_draft_untouched(h)
        self.assert_no_transmission(h)

    def test_sms_intent_prefills_without_transmitting_or_requesting_permission_for_empty_draft(self):
        h = self.harness(draft="")
        with patch("veliqen_link.app.Gtk.MessageDialog") as dialog:
            h._handle_intent(parse_intent("sms:+222?body=Hello%20there"))
        self.assertEqual(h.sms_number.text, "+222")
        self.assertEqual(h.sms_body.get_buffer().text, "Hello there")
        self.assertIsNone(h.selected_thread)
        h.select_page.assert_called_once_with("messages")
        dialog.assert_not_called()
        self.assert_no_transmission(h)

    def test_cancelled_sms_intent_keeps_recipient_body_thread_and_view(self):
        h = self.harness()
        with patch("veliqen_link.app.Gtk.MessageDialog") as factory:
            factory.return_value.run.return_value = Gtk.ResponseType.CANCEL
            h._handle_intent(parse_intent("sms:+222?body=Replacement"))
            factory.return_value.set_default_response.assert_called_once_with(Gtk.ResponseType.CANCEL)
            factory.return_value.destroy.assert_called_once()
        self.assert_draft_untouched(h)
        h.select_page.assert_not_called()
        self.assert_no_transmission(h)

    def test_accepted_sms_intent_replaces_draft_but_still_does_not_send(self):
        h = self.harness()
        with patch("veliqen_link.app.Gtk.MessageDialog") as factory:
            factory.return_value.run.return_value = Gtk.ResponseType.ACCEPT
            h._handle_intent(parse_intent("sms:+222?body=Replacement"))
        self.assertEqual(h.sms_number.text, "+222")
        self.assertEqual(h.sms_body.get_buffer().text, "Replacement")
        self.assert_no_transmission(h)

    def test_cancelled_new_message_does_not_unselect_or_clear_anything(self):
        h = self.harness()
        previous_row = h.threads.selected
        with patch("veliqen_link.app.Gtk.MessageDialog") as factory:
            factory.return_value.run.return_value = Gtk.ResponseType.DELETE_EVENT
            self.assertFalse(h._new_message())
        self.assertIs(h.threads.selected, previous_row)
        self.assert_draft_untouched(h)
        self.assert_no_transmission(h)

    def test_cancelled_thread_switch_restores_selection_without_recursive_prompt(self):
        h = self.harness()
        old, new = h.threads.rows
        with patch("veliqen_link.app.Gtk.MessageDialog") as factory:
            factory.return_value.run.return_value = Gtk.ResponseType.CANCEL
            h.threads.select_row(new)
            self.assertEqual(factory.call_count, 1)
        self.assertIs(h.threads.selected, old)
        self.assert_draft_untouched(h)
        self.assert_no_transmission(h)

    def test_cancelled_device_switch_restores_device_and_preserves_draft(self):
        h = self.harness()
        selector = Mock()
        selector.get_active_id.return_value = "phone_b"
        selector.set_active_id.side_effect = lambda *_: h._device_changed(selector)
        with patch("veliqen_link.app.Gtk.MessageDialog") as factory:
            factory.return_value.run.return_value = Gtk.ResponseType.CANCEL
            h._device_changed(selector)
            self.assertEqual(factory.call_count, 1)
        self.assertEqual(h.current_device, "phone_a")
        selector.set_active_id.assert_called_once_with("phone_a")
        h._save.assert_not_called()
        h._render_devices.assert_not_called()
        h._render_calls.assert_not_called()
        self.assert_draft_untouched(h)
        self.assert_no_transmission(h)


if __name__ == "__main__":
    unittest.main()
