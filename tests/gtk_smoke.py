"""Render the real GTK UI with isolated fixture services; never contact a phone.

Run under a disposable X display and private session bus:
  /usr/bin/python3 tests/gtk_smoke.py /absolute/output/directory
The screenshots contain synthetic names/numbers/messages, not live phone data.
"""
from pathlib import Path
import json
import sys
import tempfile
import time
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'linux'))
import gi
gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
from gi.repository import Gdk, Gtk, GLib
from veliqen_link import app as module
from veliqen_link.config import Config

OUTPUT = Path(sys.argv[1]).resolve()
OUTPUT.mkdir(parents=True, exist_ok=True)


def settle(seconds=0.25):
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


with tempfile.TemporaryDirectory(prefix='veliqen-ui-fixture-') as temporary:
    config = Config(temporary)
    config.set(appearance='light', auto_audio=False,
               bluetooth_devices={'fixture_phone': 'AA:BB:CC:DD:EE:01'})
    kde = Mock()
    kde.conversations.return_value = [
        {'thread_id': 1, 'addresses': ['+381 60 000 00 01'], 'body': 'Хорошо, до встречи!'},
        {'thread_id': 2, 'addresses': ['+381 60 000 00 02'], 'body': 'Документы отправлены.'}]
    kde.messages.return_value = [
        {'body': 'Привет! Во сколько встречаемся?', 'date': 1795251600000, 'incoming': True},
        {'body': 'В 18:30. Адрес сейчас отправлю.', 'date': 1795251660000, 'incoming': False},
        {'body': 'Хорошо, до встречи!', 'date': 1795251720000, 'incoming': True}]
    receiver = Mock()
    receiver.start.return_value = {'listening': True}
    audio = Mock()
    audio.snapshot.return_value = {'available': True, 'active': False,
        'local_sources': [{'name': 'mic', 'description': 'Микрофон ноутбука'}],
        'local_sinks': [{'name': 'speaker', 'description': 'Динамики ноутбука'}]}
    audio.default_devices.return_value = {'source': 'mic', 'sink': 'speaker'}
    clipboard = Mock()
    clipboard.available.return_value = True
    with patch.object(module, 'Config', return_value=config), \
         patch.object(module, 'KdeBackend', return_value=kde), \
         patch.object(module, 'ScreenReceiver', return_value=receiver), \
         patch.object(module, 'AudioRouter', return_value=audio), \
         patch.object(module, 'ClipboardBridge', return_value=clipboard), \
         patch.object(module.LinkApplication, '_create_indicator'):
        application = module.LinkApplication()
        application.telephony = Mock()
        application.send_notification = Mock()
        application.withdraw_notification = Mock()
        def work(operation, callback=None, message=None, busy=True, on_error=None):
            try:
                value = operation()
            except Exception as error:
                if on_error:
                    on_error(str(error))
                else:
                    raise
            else:
                if callback:
                    callback(value)
        application.work = work
        application.register(None)
        application._build_window()
        application.window.show_all()
        application.window.resize(1000, 740)
        settle()
        report = {'fixture': True, 'checks': [], 'screenshots': []}
        def capture(name, page):
            application.select_page(page)
            application.status_revealer.set_reveal_child(False)
            application.window.show_all()
            settle()
            window = application.window.get_window()
            width, height = window.get_width(), window.get_height()
            image = Gdk.pixbuf_get_from_window(window, 0, 0, width, height)
            image.savev(str(OUTPUT / (name + '.png')), 'png', [], [])
            scroll = application.stack.get_visible_child()
            horizontal = scroll.get_hadjustment()
            report['screenshots'].append({'file': name + '.png', 'width': width, 'height': height,
                'horizontal_overflow': round(horizontal.get_upper()-horizontal.get_page_size(), 1),
                'vertical_overflow': round(scroll.get_vadjustment().get_upper()-scroll.get_vadjustment().get_page_size(), 1)})
        capture('ubuntu-setup', 'overview')
        gateway = {'path': '/fixture/gateway', 'address': 'AA:BB:CC:DD:EE:01', 'name': 'VELIQEN Phone'}
        application.current_device = 'fixture_phone'
        application.devices = [{'id': 'fixture_phone', 'name': 'VELIQEN Phone', 'paired': True, 'reachable': True}]
        application.calls_state = {'available': True, 'gateways': [gateway], 'calls': []}
        application._render_devices()
        application.battery_label.set_text('82%')
        application._render_calls()
        application.refresh = Mock()
        capture('ubuntu-home', 'overview')
        assert not application.home_intro.get_visible()
        assert application.home_actions.get_visible()
        application._handle_intent({'page': 'calls', 'number': '+381 60 000 00 01'})
        application.telephony.dial.assert_not_called()
        capture('ubuntu-calls', 'calls')
        assert set(application.dialpad.keys) == set('0123456789*#')
        assert application.dial_button.get_sensitive()
        report['checks'].append('12 visible digit keys and tel intent prefills without dialing')
        application.select_page('messages')
        application.threads.select_row(application.threads.get_row_at_index(0))
        settle()
        capture('ubuntu-messages', 'messages')
        application.sms_body.get_buffer().set_text('Черновик ответа')
        kde.messages.reset_mock()
        application._load_conversations()
        kde.messages.assert_called_once_with('fixture_phone', 1)
        buffer = application.sms_body.get_buffer()
        assert buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True) == 'Черновик ответа'
        buffer.set_text('')
        report['checks'].append('Refresh updates the selected message history without erasing its draft')
        application.thread_search.set_text('Документы')
        application.threads.invalidate_filter()
        settle()
        assert not application.threads.get_row_at_index(0).get_child_visible()
        assert application.threads.get_row_at_index(1).get_child_visible()
        application.thread_search.set_text('')
        application.threads.invalidate_filter()
        report['checks'].append('Conversation search hides unrelated threads')
        active = {'path': '/fixture/call', 'gateway_path': gateway['path'], 'address': gateway['address'],
                  'number': '+381 60 000 00 01', 'state': 'active'}
        application.calls_state['calls'] = [active]
        application._render_calls()
        capture('ubuntu-active-call', 'calls')
        assert application.dialpad.mode == 'tones'
        assert not application.dial_button.get_sensitive()
        assert not application.call_banner.get_reveal_child()
        application.dialpad.keys['5'].clicked()
        application.telephony.send_call_tones.assert_called_once()
        application.telephony.dial.assert_not_called()
        report['checks'].append('Active call digit sends a tone to the exact call; no second dial')
        active['state'] = 'incoming'
        application._render_calls()
        capture('ubuntu-incoming', 'messages')
        assert application.call_banner.get_reveal_child()
        application.calls_state['calls'] = []
        application._render_calls()
        for page in ('files', 'screen', 'settings'):
            capture('ubuntu-' + page, page)
        application.appearance_selector.set_active_id('dark')
        capture('ubuntu-calls-dark', 'calls')
        application.appearance_selector.set_active_id('light')
        application.window.resize(820, 580)
        settle()
        capture('ubuntu-calls-compact', 'calls')
        capture('ubuntu-messages-compact', 'messages')
        report['checks'].append('Light/dark settings change real GTK CSS; compact window renders')
        (OUTPUT / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False))
        application.closing = True
        application.pool.shutdown(wait=False, cancel_futures=True)
        application.screen_input_pool.shutdown(wait=False, cancel_futures=True)
        application.window.destroy()
        application.quit()
