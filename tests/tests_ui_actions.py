"""Regression tests for asynchronous user actions, without a display/server.

These exercise the real LinkApplication handlers using controlled completion
order. No Gtk.Application is constructed, no clipboard is read, no phone is
contacted and no SMS is sent. In particular, a changed draft or a response for
another device must survive completion of an earlier operation.
"""

from dataclasses import dataclass
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))

from veliqen_link import app
from veliqen_link.kde import KdeBackend, KdeError


class TextField:
    def __init__(self, text=""):
        self.text = text

    def get_text(self):
        return self.text

    def set_text(self, text):
        self.text = text


class TextBuffer(TextField):
    def get_start_iter(self):
        return 0

    def get_end_iter(self):
        return len(self.text)

    def get_text(self, start, end, _include_hidden):
        return self.text[start:end]


class TextView:
    def __init__(self, text=""):
        self.buffer = TextBuffer(text)

    def get_buffer(self):
        return self.buffer


class Button:
    def __init__(self):
        self.sensitive = True

    def set_sensitive(self, value):
        self.sensitive = bool(value)


class Rows:
    def __init__(self):
        self.children = []

    def get_children(self):
        return list(self.children)

    def remove(self, child):
        self.children.remove(child)


@dataclass
class Job:
    operation: object
    callback: object

    def run(self):
        return self.operation()

    def finish(self, value):
        if self.callback:
            self.callback(value)


class Scheduler:
    def __init__(self):
        self.idles = []
        self.timeouts = []

    def idle_add(self, callback, *args, **_kwargs):
        self.idles.append((callback, args))
        return len(self.idles)

    def timeout_add_seconds(self, interval, callback, *args, **_kwargs):
        self.timeouts.append((interval, callback, args))
        return len(self.timeouts)

    def drain(self):
        count = 0
        while self.idles:
            callback, args = self.idles.pop(0)
            callback(*args)
            count += 1
            if count > 100:
                raise AssertionError("Unexpected endless idle queue")


class ActionHarness:
    # Bind the production handlers, not copies of their decision logic.
    _send_sms = app.LinkApplication._send_sms
    _device = app.LinkApplication._device
    _select_device = app.LinkApplication._select_device
    _device_changed = app.LinkApplication._device_changed
    _clipboard_changed = app.LinkApplication._clipboard_changed
    _clipboard_finished = app.LinkApplication._clipboard_finished
    _load_conversations = app.LinkApplication._load_conversations

    def __init__(self):
        self.current_device = "phone_a"
        self.devices = [{"id": name, "paired": True, "reachable": True}
                        for name in ("phone_a", "phone_b")]
        self.config = {"clipboard_sync": True}
        self.sms_pending = False
        self.clipboard_pending = False
        self.clipboard_queued = None
        self.selected_thread = None
        self._conversation_pending = False
        self._confirm_new_message = Mock(return_value=True)
        self._render_calls = Mock()
        self.sms_number = TextField("+381600000001")
        self.sms_sim = TextField("2")
        self.sms_body = TextView("Первое сообщение")
        self.sms_send_button = Button()
        self.message_view = TextView("Исходная переписка")
        self.threads = Rows()
        self.kde = Mock(spec=KdeBackend)
        self.kde.send_sms.return_value = {"status": "requested"}
        self.kde.conversations.return_value = [{"thread_id": 42, "body": "Данные первого телефона",
                                               "addresses": ["+381600000001"]}]
        self.jobs = []
        self.status_messages = []
        self.saved = []

    def work(self, operation, callback=None, message=None, busy=True, **_kwargs):
        self.jobs.append(Job(operation, callback))

    def say(self, message, error=False):
        self.status_messages.append((message, error))

    def _save(self, **values):
        self.saved.append(values)

    def _render_devices(self):
        pass

    def change_device(self, device):
        selector = Mock()
        selector.get_active_id.return_value = device
        self._device_changed(selector)


class AsyncActionTests(unittest.TestCase):
    def setUp(self):
        self.ui = ActionHarness()
        self.scheduler = Scheduler()
        self.addCleanup(patch.stopall)
        patch.object(app.GLib, "idle_add", self.scheduler.idle_add).start()
        patch.object(app.GLib, "timeout_add_seconds", self.scheduler.timeout_add_seconds).start()

    def test_sms_double_click_and_click_before_completion_send_once(self):
        self.ui._send_sms()
        self.ui._send_sms()
        self.assertEqual(len(self.ui.jobs), 1)
        self.assertFalse(self.ui.sms_send_button.sensitive)
        job = self.ui.jobs.pop(0)
        value = job.run()
        # The request has completed in the worker, but its UI callback has not.
        self.ui._send_sms()
        self.assertEqual(self.ui.jobs, [])
        self.ui.kde.send_sms.assert_called_once_with("phone_a", "+381600000001", "Первое сообщение", 2)
        job.finish(value)
        self.assertTrue(self.ui.sms_send_button.sensitive)
        self.assertEqual(self.ui.sms_body.buffer.text, "")

    def test_success_does_not_erase_a_draft_edited_while_request_is_pending(self):
        self.ui._send_sms()
        job = self.ui.jobs.pop(0)
        value = job.run()
        self.ui.sms_body.buffer.set_text("Следующий черновик")
        job.finish(value)
        self.assertEqual(self.ui.sms_body.buffer.text, "Следующий черновик")
        self.assertFalse(self.ui.sms_pending)

    def test_success_does_not_erase_same_text_addressed_to_a_new_recipient(self):
        self.ui._send_sms()
        job = self.ui.jobs.pop(0)
        self.ui.sms_number.set_text("+381600000002")
        job.finish(job.run())
        self.assertEqual(self.ui.sms_body.buffer.text, "Первое сообщение")
        self.assertEqual(self.ui.sms_number.text, "+381600000002")

    def test_device_change_clears_recipient_and_sim_and_keeps_the_new_draft(self):
        self.ui._send_sms()
        job = self.ui.jobs.pop(0)
        self.ui.change_device("phone_b")
        self.assertEqual(self.ui.sms_number.text, "")
        self.assertEqual(self.ui.sms_sim.text, "")
        # Even an identical-looking new draft belongs to the newly selected phone.
        self.ui.sms_number.set_text("+381600000001")
        self.ui.sms_body.buffer.set_text("Первое сообщение")
        job.finish(job.run())
        self.assertEqual(self.ui.sms_body.buffer.text, "Первое сообщение")
        self.ui.kde.send_sms.assert_called_once_with("phone_a", "+381600000001", "Первое сообщение", 2)
        self.assertEqual(self.scheduler.timeouts, [])

    def test_cancelled_device_change_keeps_the_original_recipient_and_draft(self):
        self.ui._confirm_new_message.return_value = False
        self.ui.change_device("phone_b")
        self.assertEqual(self.ui.current_device, "phone_a")
        self.assertEqual(self.ui.sms_number.text, "+381600000001")
        self.assertEqual(self.ui.sms_body.buffer.text, "Первое сообщение")
        self.assertEqual(self.ui.saved, [])
        self.ui._render_calls.assert_not_called()

    def test_device_change_immediately_refreshes_dialpad_target(self):
        self.ui.change_device("phone_b")
        self.ui._render_calls.assert_called_once()

    def test_sms_failure_retains_draft_unlocks_button_and_never_retries(self):
        self.ui.kde.send_sms.side_effect = KdeError("Связь потеряна", uncertain=True)
        self.ui._send_sms()
        job = self.ui.jobs.pop(0)
        with self.assertRaises(KdeError):
            job.run()
        self.scheduler.drain()
        self.assertTrue(self.ui.sms_send_button.sensitive)
        self.assertFalse(self.ui.sms_pending)
        self.assertEqual(self.ui.sms_body.buffer.text, "Первое сообщение")
        self.ui.kde.send_sms.assert_called_once()
        self.assertEqual(self.ui.jobs, [])
        self.assertEqual(self.scheduler.timeouts, [])

    def test_clipboard_keeps_the_latest_copy_while_an_earlier_send_is_busy(self):
        self.ui._clipboard_changed("Первый")
        self.ui._clipboard_changed("Второй")
        self.ui._clipboard_changed("Последний")
        self.assertEqual(len(self.ui.jobs), 1)
        self.ui.jobs.pop(0).run()
        self.scheduler.drain()
        self.assertEqual(len(self.ui.jobs), 1)
        self.ui.jobs.pop(0).run()
        self.scheduler.drain()
        self.assertEqual(self.ui.kde.send_clipboard.call_args_list,
                         [call("phone_a", "Первый"), call("phone_a", "Последний")])
        self.assertEqual(self.ui.jobs, [])
        self.assertFalse(self.ui.clipboard_pending)

    def test_disabling_clipboard_sync_cancels_the_waiting_latest_copy(self):
        self.ui._clipboard_changed("Начатый")
        self.ui._clipboard_changed("Ожидающий")
        self.ui.config["clipboard_sync"] = False
        self.ui.jobs.pop(0).run()
        self.scheduler.drain()
        self.ui.kde.send_clipboard.assert_called_once_with("phone_a", "Начатый")
        self.assertEqual(self.ui.jobs, [])

    def test_queued_clipboard_for_one_phone_is_not_sent_to_another_phone(self):
        self.ui._clipboard_changed("Начатый")
        self.ui._clipboard_changed("Ожидающий")
        self.ui.change_device("phone_b")
        self.ui.jobs.pop(0).run()
        self.scheduler.drain()
        self.ui.kde.send_clipboard.assert_called_once_with("phone_a", "Начатый")
        self.assertEqual(self.ui.jobs, [])

    def test_late_conversations_response_cannot_replace_another_phones_threads(self):
        self.ui._load_conversations()
        job = self.ui.jobs.pop(0)
        value = job.run()
        self.ui.change_device("phone_b")
        self.ui.threads.children = ["Переписка второго телефона"]
        self.ui.message_view.buffer.set_text("Сообщения второго телефона")
        with patch.object(app, "clear", side_effect=AssertionError("A stale response attempted to render")):
            job.finish(value)
        self.assertEqual(self.ui.threads.children, ["Переписка второго телефона"])
        self.assertEqual(self.ui.message_view.buffer.text, "Сообщения второго телефона")


if __name__ == "__main__":
    unittest.main()
