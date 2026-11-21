"""Device selection through the real pairing dialog's callbacks and timers."""
import contextlib
from pathlib import Path
import sys
from types import MethodType
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link import app
import test_intent_ui as fields


class DeviceSelectionTests(unittest.TestCase):
    def harness(self):
        h = fields.IntentCallbackTests().harness()
        h.pair_dialog = None
        h.refresh = Mock()
        h._discover = Mock()
        h._network_settings = Mock()
        h.device_selector = Mock()
        h.stack = Mock()
        h.stack.get_visible_child_name.return_value = "overview"
        h._load_conversations = Mock()
        h._conversation_pending = False
        h.devices = [{"id": "phone_a", "paired": True, "reachable": True}]
        h.message_device_loaded = "phone_a"
        h._pair_dialog = MethodType(app.LinkApplication._pair_dialog, h)
        return h

    def run_pair_dialog(self, h, snapshots, inside, confirm):
        actions, timers = {}, {}
        dialog = Mock()
        h._discover.side_effect = snapshots

        def button(text, callback, *args):
            actions.setdefault(text, []).append(callback)
            return Mock()

        def box(*args, **kwargs):
            widget = Mock()
            widget.get_children.return_value = []
            return widget

        def work(operation, callback=None, *args, **kwargs):
            value = operation()
            if callback:
                callback(value)

        def timer(_seconds, callback):
            timers["poll"] = callback
            return 42

        h.work = work
        dialog.run.side_effect = lambda: inside(actions, timers, dialog)
        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(app.Gtk, "Dialog", return_value=dialog))
            confirmation = stack.enter_context(patch.object(app.Gtk, "MessageDialog"))
            confirmation.return_value.run.side_effect = lambda: confirm(actions, timers, confirmation)
            stack.enter_context(patch.object(app, "box", side_effect=box))
            stack.enter_context(patch.object(app, "label", side_effect=lambda *a, **k: Mock()))
            stack.enter_context(patch.object(app, "button", side_effect=button))
            stack.enter_context(patch.object(app, "style", side_effect=lambda widget, _name: widget))
            stack.enter_context(patch.object(app.GLib, "timeout_add_seconds", side_effect=timer))
            remove = stack.enter_context(patch.object(app.GLib, "source_remove"))
            h._pair_dialog()
            remove.assert_called_once_with(42)
        self.assertIsNone(h.pair_dialog)
        dialog.destroy.assert_called_once()
        return dialog, confirmation

    def test_paired_dialog_selects_new_phone_before_it_exists_in_selector(self):
        h = self.harness()
        phone = {"id": "phone_b", "name": "New phone", "paired": True, "reachable": True}
        dialog, confirmation = self.run_pair_dialog(h, [[phone]],
            lambda actions, timers, dialog: actions["Выбрать"][0](),
            lambda *args: app.Gtk.ResponseType.ACCEPT)
        self.assertEqual(h.current_device, "phone_b")
        self.assertNotIn("phone_b", [d["id"] for d in h.devices])
        h.device_selector.set_active_id.assert_not_called()
        h._save.assert_called_once_with(device_id="phone_b")
        self.assertEqual(h.sms_number.text, "")
        self.assertEqual(h.sms_sim.text, "")
        self.assertEqual(h.sms_body.get_buffer().text, "")
        self.assertEqual(h.threads.rows, [])
        self.assertIsNone(h.selected_thread)
        self.assertIsNone(h.message_device_loaded)
        self.assertEqual(h.message_view.get_buffer().text, "Нажми «Обновить переписку».")
        h._render_devices.assert_called_once()
        h._render_calls.assert_called_once()
        self.assertEqual(confirmation.call_count, 1)
        dialog.response.assert_called_once_with(app.Gtk.ResponseType.CLOSE)

    def test_cancel_manual_selection_preserves_draft_and_blocks_nested_choose_and_poll(self):
        h = self.harness()
        phone = {"id": "phone_b", "paired": True, "reachable": True}

        def confirm(actions, timers, factory):
            before = h._discover.call_count
            self.assertFalse(actions["Выбрать"][0]())
            self.assertTrue(timers["poll"]())
            self.assertEqual(h._discover.call_count, before)
            self.assertEqual(factory.call_count, 1)
            return app.Gtk.ResponseType.CANCEL

        dialog, confirmation = self.run_pair_dialog(h, [[phone]],
            lambda actions, timers, dialog: actions["Выбрать"][0](), confirm)
        self.assertEqual(h.current_device, "phone_a")
        fields.IntentCallbackTests().assert_draft_untouched(h)
        self.assertEqual(h.message_device_loaded, "phone_a")
        h._save.assert_not_called()
        h._render_calls.assert_not_called()
        dialog.response.assert_not_called()
        self.assertEqual(confirmation.call_count, 1)

    def test_newly_paired_target_is_cleared_before_prompt_and_does_not_reprompt_after_cancel(self):
        h = self.harness()
        unpaired = {"id": "phone_b", "paired": False, "reachable": True}
        paired = {**unpaired, "paired": True}

        def confirm(actions, timers, factory):
            callback = timers["poll"]
            closure = dict(zip(callback.__code__.co_freevars, (cell.cell_contents for cell in callback.__closure__)))
            self.assertIsNone(closure["progress"]["target"])
            self.assertTrue(closure["progress"]["selecting"])
            before = h._discover.call_count
            callback()
            self.assertEqual(h._discover.call_count, before)
            return app.Gtk.ResponseType.CANCEL

        def inside(actions, timers, dialog):
            actions["Подключить"][0]()
            # A completed pairing stays paired, but its cancelled selection
            # must not start another prompt on subsequent discovery ticks.
            timers["poll"]()
            timers["poll"]()

        dialog, confirmation = self.run_pair_dialog(h, [[unpaired], [paired], [paired], [paired]], inside, confirm)
        h.kde.pair.assert_called_once_with("phone_b")
        self.assertEqual(confirmation.call_count, 1)
        self.assertEqual(h.current_device, "phone_a")
        fields.IntentCallbackTests().assert_draft_untouched(h)
        dialog.response.assert_not_called()

    def test_newly_paired_phone_is_selected_only_after_draft_confirmation(self):
        h = self.harness()
        unpaired = {"id": "phone_b", "paired": False, "reachable": True}
        paired = {**unpaired, "paired": True}
        dialog, confirmation = self.run_pair_dialog(h, [[unpaired], [paired]],
            lambda actions, timers, dialog: actions["Подключить"][0](),
            lambda *args: app.Gtk.ResponseType.ACCEPT)
        self.assertEqual(confirmation.call_count, 1)
        self.assertEqual(h.current_device, "phone_b")
        self.assertEqual(h.sms_body.get_buffer().text, "")
        h._render_calls.assert_called_once()
        dialog.response.assert_called_once_with(app.Gtk.ResponseType.CLOSE)

    def test_shared_selection_rejects_a_reentrant_switch_without_another_prompt(self):
        h = self.harness()
        def confirmed():
            self.assertFalse(h._select_device("phone_c"))
            self.assertEqual(h.current_device, "phone_a")
            return True
        h._confirm_new_message = Mock(side_effect=confirmed)
        self.assertTrue(h._select_device("phone_b"))
        h._confirm_new_message.assert_called_once()
        self.assertEqual(h.current_device, "phone_b")
        self.assertFalse(h._selecting_device)

    def test_unpair_and_refresh_never_move_draft_to_another_paired_phone(self):
        h = self.harness()
        with patch.object(app.Gtk, "MessageDialog") as confirmation:
            confirmation.return_value.run.return_value = app.Gtk.ResponseType.OK
            app.LinkApplication._unpair(h)
        operation, done, _ = h.jobs.pop()
        done(operation())
        h.kde.unpair.assert_called_once_with("phone_a")
        h.refresh.assert_called_once()
        h._maybe_auto_audio = Mock()
        h.battery_label = Mock()
        h.audio = Mock()
        h.phone_status = Mock()
        app.LinkApplication._refreshed(h, {
            "devices": [{"id": "phone_b", "paired": True, "reachable": True}],
            "battery": {}, "calls": {"calls": [], "gateways": []}, "kde_error": ""})
        self.assertEqual(h.current_device, "phone_a")
        h._save.assert_not_called()
        h.device_selector.set_active_id.assert_called_once_with("phone_a")
        fields.IntentCallbackTests().assert_draft_untouched(h)
        self.assertIsNone(app.LinkApplication._device(h))
        h.kde.send_sms.assert_not_called()


if __name__ == "__main__":
    unittest.main()
