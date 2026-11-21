"""Native desktop UI. Device operations run outside the GTK main thread."""
import concurrent.futures
import hashlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import threading
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, Gio, GLib, Pango

from . import __version__
from .config import Config
from .clipboard import ClipboardBridge
from .kde import KdeBackend
from .telephony import TelephonyManager, TelephonyError, normalize_number
from .dialpad import Dialpad, active_call, call_key
from .audio import AudioRouter
from .screen import ScreenManager
from .bluetooth import BluetoothManager
from .screenlink import ScreenReceiver, SCREEN_REQUEST
from .screen_viewer import ScreenViewer
from .network import screen_address, open_screen_port
from .theme import stylesheet
from .message_view import MessageView
from .intents import parse_intent
from .session_setup import autostart_enabled, set_autostart, enable_clipboard_extension

CSS = stylesheet(False)

def style(widget, name):
    widget.get_style_context().add_class(name)
    return widget

def label(text="", cls=None, wrap=False):
    item = Gtk.Label(label=text, xalign=0)
    item.set_line_wrap(wrap)
    if wrap:
        item.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        item.set_max_width_chars(70)
    if cls:
        style(item, cls)
    return item

def box(vertical=True, spacing=12, cls=None):
    item = Gtk.Box(orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL, spacing=spacing)
    if cls:
        style(item, cls)
    return item

def button(text, callback, accent=False):
    item = Gtk.Button(label=text)
    if accent:
        style(item, "accent")
    item.connect("clicked", lambda *_: callback())
    return item

def entry(placeholder, secret=False):
    item = Gtk.Entry()
    item.set_placeholder_text(placeholder)
    item.set_visibility(not secret)
    item.set_hexpand(True)
    return item

def scrolled(child, height=None):
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.add(child)
    if height:
        scroll.set_min_content_height(height)
    return scroll

def clear(container):
    for child in container.get_children():
        container.remove(child)

def reveal(widget, visible):
    if visible:
        widget.set_no_show_all(False)
        widget.show_all()
        widget.set_no_show_all(True)
    else:
        widget.hide()

class LinkApplication(Gtk.Application):
    def __init__(self, background=False):
        super().__init__(application_id="tech.veliqen.Link", flags=Gio.ApplicationFlags.HANDLES_OPEN)
        self.background = background
        self.first_activation = True
        self.window = None
        self.closing = False
        self.config = Config()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="veliqen")
        self.screen_input_pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="veliqen-input")
        self.native_generation = 0
        self.kde = KdeBackend()
        self.telephony = TelephonyManager()
        self.audio = AudioRouter(telephony=self.telephony)
        self.screen = ScreenManager()
        self.bluetooth = BluetoothManager()
        self.bluetooth_state = {}
        self.bluetooth_pending = False
        self.native_viewer = None
        self.native_session = None
        self.native_frame = None
        self.native_frame_scheduled = False
        self.native_frame_lock = threading.Lock()
        self.native_request_pending = False
        self.native_request_device = None
        self.native_request_sequence = 0
        self.receiver = ScreenReceiver(
            on_session=lambda info: GLib.idle_add(self._native_session_started, info),
            on_frame=self._native_frame_received,
            on_end=lambda message, session_id: GLib.idle_add(self._native_session_ended, message, session_id),
            on_status=lambda message: GLib.idle_add(self._native_status, message))
        self.auto_audio_attempt = None
        self.audio_snapshot = {}
        self.message_device_loaded = None
        self._last_connection_state = None
        self.pair_dialog = None
        self.devices = []
        self.calls_state = {"available": False, "gateways": [], "calls": []}
        self.current_device = self.config.get("device_id")
        self.clipboard_bridge = None
        self.refresh_pending = False
        self.clipboard_pending = False
        self.clipboard_queued = None
        self.sms_pending = False
        self.dial_pending = False
        self._selecting_page = False
        self.nav = {}
        self.selected_thread = None
        self.auto_route_pending = False
        self.incoming_notifications = {}
        self._status_generation = 0
        self._appearance_provider = None
        self._system_dark = False
        self._interface_settings = None
        self._conversation_items = []
        self._conversation_pending = False
        self._extension_enable_pending = False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        settings = Gtk.Settings.get_default()
        self._system_dark = settings.get_property("gtk-application-prefer-dark-theme")
        try:
            schema = Gio.SettingsSchemaSource.get_default().lookup("org.gnome.desktop.interface", True)
            if schema and schema.has_key("color-scheme"):
                self._interface_settings = Gio.Settings.new_full(schema, None, None)
                self._interface_settings.connect("changed::color-scheme", lambda *_: self._apply_appearance())
        except (GLib.Error, AttributeError):
            pass
        settings.connect("notify::gtk-theme-name", lambda *_: self._apply_appearance())
        self._apply_appearance()
        action = Gio.SimpleAction.new("show", None)
        action.connect("activate", lambda *_: self._present())
        self.add_action(action)
        for index, page in enumerate(("overview", "messages", "calls", "files"), 1):
            navigate = Gio.SimpleAction.new("page-" + page, None)
            navigate.connect("activate", lambda _a, _v, value=page: self._open_page(value))
            self.add_action(navigate)
            self.set_accels_for_action("app.page-" + page, ["<Primary>" + str(index)])
        for name, handler in [("answer", self._answer), ("hangup", self._hangup)]:
            call_action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            call_action.connect("activate", lambda action, value, fn=handler: fn(value.get_string()))
            self.add_action(call_action)
        signal.signal(signal.SIGTERM, lambda *_: GLib.idle_add(self.exit_application))

    def do_activate(self):
        if self.window is None:
            self._build_window()
            self.refresh()
            GLib.timeout_add_seconds(5, self._poll)
            self.work(self.kde.start_service, self._service_started, busy=False)
            try:
                self.telephony.watch(lambda *_: GLib.idle_add(self.refresh))
            except Exception:
                pass
        if not (self.first_activation and self.background):
            self._present()
        self.first_activation = False

    def do_open(self, files, number, hint):
        self.do_activate()
        self._present()
        paths = []
        for item in files:
            path = item.get_path()
            if path:
                paths.append(path)
                continue
            try:
                intent = parse_intent(item.get_uri())
                if intent:
                    self._handle_intent(intent)
            except ValueError as exc:
                self.say(str(exc), error=True)
        if paths:
            self.select_page("files")
            self._share_paths(paths)

    def _open_page(self, page):
        self._present()
        self.select_page(page)

    def _handle_intent(self, intent):
        page = intent["page"]
        if page == "calls" and intent.get("number"):
            self._pick_number(intent["number"])
        elif page == "messages" and intent.get("number"):
            if not self._new_message():
                return
            self.sms_number.set_text(intent["number"])
            self.sms_body.get_buffer().set_text(intent.get("body", ""))
            self.select_page("messages")
        elif page == "screen":
            self._request_native_screen()
        else:
            self.select_page(page)

    def _apply_appearance(self):
        settings = Gtk.Settings.get_default()
        mode = self.config.get("appearance")
        if mode == "dark":
            dark = True
        elif mode == "light":
            dark = False
        else:
            scheme = self._interface_settings.get_string("color-scheme") if self._interface_settings else "default"
            dark = scheme == "prefer-dark" if scheme != "default" else (self._system_dark or "dark" in (settings.get_property("gtk-theme-name") or "").lower())
        settings.set_property("gtk-application-prefer-dark-theme", bool(dark))
        if self._appearance_provider is None:
            self._appearance_provider = Gtk.CssProvider()
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), self._appearance_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self._appearance_provider.load_from_data(stylesheet(dark))

    def _present(self):
        if self.window:
            self.window.show_all()
            self.window.present()

    def _service_started(self, _):
        self.refresh()

    def work(self, operation, callback=None, message=None, busy=True, on_error=None):
        if self.closing:
            return
        if busy:
            self.say(message or "Выполняется…")
        future = self.pool.submit(operation)
        def done(job):
            try:
                value = job.result()
            except Exception as exc:
                GLib.idle_add(self._work_error, str(exc), busy, on_error)
            else:
                GLib.idle_add(self._work_done, value, callback, busy)
        future.add_done_callback(done)

    def _work_error(self, text, visible, callback=None):
        if not self.closing and callback:
            callback(text)
        if not self.closing and visible:
            self.say(text or "Операцию выполнить не удалось.", error=True)
        return False

    def _work_done(self, value, callback, visible):
        if self.closing:
            return False
        if callback:
            callback(value)
        elif visible:
            self.say("Готово.")
        return False

    def say(self, text, error=False):
        if not self.window:
            return
        self.status_label.set_text(str(text))
        context = self.status_label.get_style_context()
        if error:
            context.add_class("status-error")
        else:
            context.remove_class("status-error")
        if hasattr(self, "status_revealer"):
            self._status_generation += 1
            generation = self._status_generation
            self.status_revealer.set_reveal_child(bool(text))
            if not error:
                def hide():
                    if generation == self._status_generation and not self.closing:
                        self.status_revealer.set_reveal_child(False)
                    return False
                GLib.timeout_add_seconds(8, hide)

    def _build_window(self):
        self.window = Gtk.ApplicationWindow(application=self, title="VELIQEN Link")
        self.window.set_default_size(1000, 740)
        self.window.set_size_request(820, 580)
        self.window.connect("delete-event", self._close_window)
        header = Gtk.HeaderBar(title="VELIQEN Link", show_close_button=True)
        refresh = style(Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON), "quiet")
        refresh.set_tooltip_text("Обновить подключение")
        refresh.connect("clicked", lambda *_: self.refresh())
        header.pack_end(refresh)
        self.window.set_titlebar(header)
        root = box(False, 0)
        self.window.add(root)
        sidebar = box(spacing=4, cls="sidebar")
        sidebar.set_size_request(190, -1)
        phone = style(Gtk.Button(), "quiet")
        phone.set_tooltip_text("Выбрать или подключить телефон")
        phone.connect("clicked", lambda *_: self._pair_dialog())
        identity = box(spacing=6, cls="phone-sidebar")
        identity.pack_start(Gtk.Image.new_from_icon_name("phone-symbolic", Gtk.IconSize.DIALOG), False, False, 0)
        self.header_device = label("Твой телефон", "phone-name")
        self.header_device.set_ellipsize(Pango.EllipsizeMode.END)
        self.header_device.set_max_width_chars(20)
        self.header_device.set_xalign(0.5)
        self.header_state = label("Подключить", "small")
        self.header_state.set_xalign(0.5)
        self.battery_label = label("", "small")
        self.battery_label.set_xalign(0.5)
        for widget in (self.header_device, self.header_state, self.battery_label):
            identity.pack_start(widget, False, False, 0)
        phone.add(identity)
        sidebar.pack_start(phone, False, False, 0)
        self.device_selector = Gtk.ComboBoxText()
        self.device_selector.connect("changed", self._device_changed)
        self.device_selector.set_no_show_all(True)
        sidebar.pack_start(self.device_selector, False, False, 4)
        sidebar.pack_start(Gtk.Separator(), False, False, 8)
        for key, title, icon in [("overview", "Обзор", "view-grid-symbolic"), ("messages", "Сообщения", "mail-unread-symbolic"),
                                ("calls", "Звонки", "call-start-symbolic"), ("files", "Файлы", "folder-symbolic")]:
            nav = style(Gtk.ToggleButton(), "nav")
            row = box(False, 10)
            row.pack_start(Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON), False, False, 0)
            row.pack_start(label(title), True, True, 0)
            nav.add(row)
            nav.connect("clicked", lambda w, page=key: self.select_page(page))
            sidebar.pack_start(nav, False, False, 0)
            self.nav[key] = nav
        settings = style(Gtk.ToggleButton(label="Настройки"), "nav")
        settings.get_child().set_xalign(0)
        settings.connect("clicked", lambda *_: self.select_page("settings"))
        self.nav["settings"] = settings
        sidebar.pack_end(settings, False, False, 0)
        sidebar.pack_end(style(button("Экран телефона", self._request_native_screen), "quiet"), False, False, 6)
        root.pack_start(sidebar, False, False, 0)
        content = box(spacing=0)
        root.pack_start(content, True, True, 0)
        self.call_banner = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.call_banner_box = box(False, 10, "call-banner")
        self.call_banner.add(self.call_banner_box)
        content.pack_start(self.call_banner, False, False, 0)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=100)
        content.pack_start(self.stack, True, True, 0)
        for key, builder in [("overview", self._overview_page), ("messages", self._messages_page),
                             ("calls", self._calls_page), ("files", self._files_page),
                             ("screen", self._screen_page), ("settings", self._settings_page)]:
            self.stack.add_named(scrolled(builder()), key)
        self.status_label = style(label("", wrap=True), "status")
        self.status_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_UP)
        self.status_revealer.add(self.status_label)
        content.pack_end(self.status_revealer, False, False, 0)
        self.select_page("overview")
        try:
            self.clipboard_bridge = ClipboardBridge(self._clipboard_changed)
            self.clipboard_bridge.set_enabled(self.config.get("clipboard_sync"))
        except Exception:
            pass
        self._render_devices()
        self._create_indicator()

    def _action_card(self, title, subtitle, icon_name, callback):
        action = style(Gtk.Button(), "action-card")
        layout = box(spacing=10)
        icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.LARGE_TOOLBAR)
        layout.pack_start(icon, False, False, 0)
        title_label = label(title, "action-title")
        title_label.set_xalign(0.5)
        layout.pack_start(title_label, False, False, 0)
        action.set_tooltip_text(subtitle)
        action.add(layout)
        action.connect("clicked", lambda *_: callback())
        return action

    def _step(self, number, title, subtitle):
        row = box(False, 12, "step")
        row.pack_start(label(str(number), "step-number"), False, False, 0)
        text = box(spacing=4)
        text.pack_start(label(title, "section"), False, False, 0)
        text.pack_start(label(subtitle, "muted", True), False, False, 0)
        row.pack_start(text, True, True, 0)
        return row

    def _page(self, title, subtitle):
        page = box(spacing=14, cls="page")
        page.pack_start(label(title, "heading"), False, False, 0)
        if subtitle:
            page.pack_start(label(subtitle, "muted", True), False, False, 0)
        return page

    def _overview_page(self):
        page = self._page("Обзор", "")
        hero = box(cls="card")
        self.home_intro = hero
        self.home_intro.set_no_show_all(True)
        self.phone_title = label("Подключим телефон", "section")
        self.phone_status = label("Открой VELIQEN Link на телефоне. Оба устройства должны быть в одной сети Wi-Fi.", "muted", True)
        hero.pack_start(self.phone_title, False, False, 0)
        hero.pack_start(self.phone_status, False, False, 0)
        row = box(False)
        self.home_primary = button("Подключить телефон", self._home_action, True)
        row.pack_start(self.home_primary, False, False, 0)
        self.home_secondary = style(button("Другой телефон", self._pair_dialog), "quiet")
        self.home_secondary.set_no_show_all(True)
        row.pack_start(self.home_secondary, False, False, 0)
        hero.pack_start(row, False, False, 4)
        page.pack_start(hero, False, False, 0)
        self.home_welcome = box(spacing=0)
        self.home_welcome.set_no_show_all(True)
        self.home_welcome.pack_start(label("Подтверди сопряжение на обоих устройствах, затем выбери нужные доступы на телефоне. Повторять настройку при каждом запуске не нужно.", "muted", True), False, False, 0)
        page.pack_start(self.home_welcome, False, False, 0)
        self.home_actions = Gtk.Grid(column_spacing=10, row_spacing=10, column_homogeneous=True)
        self.home_actions.set_no_show_all(True)
        for index, (title, subtitle, icon, callback) in enumerate([
            ("Экран", "Открыть экран телефона", "video-display-symbolic", self._request_native_screen),
            ("Файл", "Передать файлы на телефон", "folder-symbolic", self._choose_files),
            ("Буфер", "Передать скопированный текст", "edit-paste-symbolic", self._send_current_clipboard),
            ("Найти", "Подать звуковой сигнал на телефоне", "find-location-symbolic", lambda: self.device_action(lambda d: self.kde.ring(d))),
        ]):
            self.home_actions.attach(self._action_card(title, subtitle, icon, callback), index, 0, 1, 1)
        page.pack_start(self.home_actions, False, False, 0)
        self.home_details = box(spacing=0, cls="group")
        self.home_details.set_no_show_all(True)
        self.connection_label = label("Нет связи", "muted")
        self.call_label = label("Звонки: подключить Bluetooth", "muted")
        for title, detail, callback in [("Файлы и сообщения", self.connection_label, lambda: self.select_page("files")),
                                        ("Звонки", self.call_label, lambda: self.select_page("calls"))]:
            row = box(False, 12, "group-row")
            row.pack_start(label(title), True, True, 0)
            row.pack_start(detail, False, False, 0)
            open_button = style(Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON), "quiet")
            open_button.set_tooltip_text("Открыть «" + title + "»")
            open_button.connect("clicked", lambda _w, action=callback: action())
            row.pack_end(open_button, False, False, 0)
            self.home_details.pack_start(row, False, False, 0)
        page.pack_start(self.home_details, False, False, 0)
        self.home_clipboard = box(False, 12, "group")
        self.home_clipboard.set_no_show_all(True)
        row = box(False, 12, "group-row")
        text = box(spacing=3)
        text.pack_start(label("Автопередача буфера"), False, False, 0)
        self.home_clipboard_status = label("", "small")
        text.pack_start(self.home_clipboard_status, False, False, 0)
        row.pack_start(text, True, True, 0)
        row.pack_end(style(button("Настроить", lambda: self.select_page("settings")), "quiet"), False, False, 0)
        self.home_clipboard.pack_start(row, True, True, 0)
        page.pack_start(self.home_clipboard, False, False, 0)
        self.clipboard_entry = entry("Текст или ссылка")
        return page

    def _home_action(self):
        if any(d.get("id") == self.current_device and d.get("paired") and d.get("reachable") for d in self.devices):
            self._request_native_screen()
        else:
            self._pair_dialog()

    def _send_current_clipboard(self):
        device = self._device()
        if not device:
            return
        def received(_clipboard, text, _data):
            if device != self.current_device:
                return
            if not text:
                self.say("Скопируй текст или ссылку, затем нажми «Отправить буфер».")
                return
            self.work(lambda: self.kde.send_clipboard(device, text), lambda _: self.say("Текст передан в буфер телефона."))
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_text(received, None)

    def _messages_page(self):
        page = self._page("Сообщения", "")
        panes = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        panes.set_position(215)
        left = box(spacing=8)
        tools = box(False, 6)
        tools.pack_start(button("Новое SMS", self._new_message), True, True, 0)
        refresh = style(Gtk.Button.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON), "quiet")
        refresh.set_tooltip_text("Обновить переписки")
        refresh.connect("clicked", lambda *_: self._load_conversations())
        tools.pack_end(refresh, False, False, 0)
        left.pack_start(tools, False, False, 0)
        self.thread_search = Gtk.SearchEntry(placeholder_text="Поиск переписок")
        self.thread_search.connect("search-changed", lambda *_: self.threads.invalidate_filter())
        left.pack_start(self.thread_search, False, False, 0)
        self.threads = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.threads.connect("row-selected", self._thread_selected)
        self.threads.set_filter_func(lambda row: self.thread_search.get_text().casefold() in getattr(row, "search_text", ""))
        self.threads.set_placeholder(label("Пока нет переписок", "muted"))
        left.pack_start(scrolled(self.threads, 270), True, True, 0)
        left.set_size_request(205, -1)
        panes.pack1(left, resize=False, shrink=False)
        right = box(spacing=10)
        right.set_margin_start(12)
        recipient = box(False, 6)
        self.sms_number = entry("Кому: номер телефона")
        self.sms_number.set_tooltip_text("Получатель SMS")
        recipient.pack_start(self.sms_number, True, True, 0)
        call = style(Gtk.Button.new_from_icon_name("call-start-symbolic", Gtk.IconSize.BUTTON), "quiet")
        call.set_tooltip_text("Открыть этот номер в номеронабирателе")
        call.connect("clicked", lambda *_: self._pick_number(self.sms_number.get_text().strip()))
        recipient.pack_end(call, False, False, 0)
        right.pack_start(recipient, False, False, 0)
        self.message_view = MessageView()
        self.message_view.set_min_content_height(230)
        self.message_view.get_buffer().set_text("Переписки появятся после подключения телефона и разрешения доступа к SMS.")
        right.pack_start(self.message_view, True, True, 0)
        form = box(spacing=8, cls="composer")
        self.sms_body = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, left_margin=10, right_margin=10, top_margin=8, bottom_margin=8)
        self.sms_body.set_tooltip_text("Текст SMS. Ctrl+Enter — отправить")
        self.sms_body.connect("key-press-event", self._sms_key_press)
        form.pack_start(scrolled(self.sms_body, 70), True, True, 0)
        row = box(False, 8)
        row.pack_start(label("Ctrl+Enter — отправить", "small"), True, True, 0)
        self.sms_send_button = button("Отправить", self._send_sms, True)
        row.pack_end(self.sms_send_button, False, False, 0)
        form.pack_start(row, False, False, 0)
        extra = Gtk.Expander(label="Параметры SMS")
        extra_box = box(spacing=8)
        self.sms_sim = entry("ID другой SIM, если нужен")
        self.sms_sim.set_tooltip_text("Идентификатор подписки Android. Обычно это поле можно оставить пустым.")
        extra_box.pack_start(self.sms_sim, False, False, 0)
        extra_box.pack_start(button("Расширенная переписка", lambda: self.device_action(lambda d: self.kde.open_sms(d))), False, False, 0)
        extra.add(extra_box)
        form.pack_start(extra, False, False, 0)
        right.pack_end(form, False, False, 0)
        panes.pack2(right, resize=True, shrink=False)
        page.pack_start(panes, True, True, 0)
        return page

    def _sms_key_press(self, _widget, event):
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and event.state & Gdk.ModifierType.CONTROL_MASK:
            self._send_sms()
            return True
        return False

    def _confirm_new_message(self):
        buffer = self.sms_body.get_buffer()
        if not buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True).strip():
            return True
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.NONE,
            text="Заменить неотправленный текст?")
        dialog.format_secondary_text("В текущем сообщении есть черновик. Продолжение очистит его.")
        dialog.add_buttons("Оставить черновик", Gtk.ResponseType.CANCEL, "Продолжить", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.CANCEL)
        accepted = dialog.run() == Gtk.ResponseType.ACCEPT
        dialog.destroy()
        return accepted

    def _new_message(self):
        if not self._confirm_new_message():
            return False
        self.selected_thread = None
        self.threads.unselect_all()
        self.sms_number.set_text("")
        self.sms_body.get_buffer().set_text("")
        self.message_view.get_buffer().set_text("Новое SMS. Введи номер получателя и текст сообщения.")
        self.sms_number.grab_focus()
        return True

    def _calls_page(self):
        page = self._page("Звонки", "")
        page.set_spacing(10)
        setup = box(spacing=8)
        self.telephony_status = label("Проверяем готовность звонков…", "section", True)
        setup.pack_start(self.telephony_status, False, False, 0)
        self.call_setup_hint = label("Подключи Bluetooth один раз. Приложение запомнит твой телефон.", "muted", True)
        setup.pack_start(self.call_setup_hint, False, False, 0)
        self.connect_calls_button = button("Подключить звонки", self._connect_calls, True)
        self.connect_calls_button.set_halign(Gtk.Align.START)
        self.connect_calls_button.set_no_show_all(True)
        setup.pack_start(self.connect_calls_button, False, False, 0)
        self.call_system_button = button("Настроить звонки на компьютере", self._enable_calls_setup)
        self.call_system_button.set_halign(Gtk.Align.START)
        self.call_system_button.set_no_show_all(True)
        setup.pack_start(self.call_system_button, False, False, 0)
        page.pack_start(setup, False, False, 0)
        self.calls_box = box()
        page.pack_start(self.calls_box, False, False, 0)
        self._tone_queue = []
        self._tone_pending = False
        self.dialpad = Dialpad(self._dial, self._send_tone)
        self.dial_number = self.dialpad.entry
        self.dial_button = self.dialpad.call_button
        page.pack_start(self.dialpad, False, False, 0)

        self.audio_expander = Gtk.Expander(label="Звук и подключение")
        audio = box(spacing=12, cls="card")
        self.audio_status = label("Звук подключится при разговоре после настройки Bluetooth.", "muted", True)
        audio.pack_start(self.audio_status, False, False, 0)
        self.auto_audio_toggle = Gtk.CheckButton(label="Использовать звук компьютера автоматически")
        self.auto_audio_toggle.set_active(self.config.get("auto_audio"))
        self.auto_audio_toggle.connect("toggled", lambda w: self._save(auto_audio=w.get_active()))
        audio.pack_start(self.auto_audio_toggle, False, False, 0)
        row = box(False)
        row.pack_start(button("Использовать звук компьютера", self._smart_start_audio), False, False, 0)
        row.pack_start(style(button("Отключить звук компьютера", self._pause_call_audio), "quiet"), False, False, 0)
        audio.pack_start(row, False, False, 0)
        grid = Gtk.Grid(column_spacing=14, row_spacing=12)
        self.microphone_selector = Gtk.ComboBoxText()
        self.speaker_selector = Gtk.ComboBoxText()
        self.microphone_selector.set_hexpand(True)
        self.speaker_selector.set_hexpand(True)
        grid.attach(label("Микрофон"), 0, 0, 1, 1)
        grid.attach(self.microphone_selector, 1, 0, 1, 1)
        grid.attach(label("Динамики"), 0, 1, 1, 1)
        grid.attach(self.speaker_selector, 1, 1, 1, 1)
        audio.pack_start(grid, False, False, 0)
        row = box(False)
        row.pack_start(button("Обновить устройства", self._refresh_audio), False, False, 0)
        row.pack_start(button("Восстановить подключение звука", self._prepare_audio), False, False, 0)
        audio.pack_start(row, False, False, 0)
        row = box(False)
        row.pack_start(style(button("Настройки Bluetooth", self._bluetooth_settings), "quiet"), False, False, 0)
        row.pack_start(style(button("Переподключить телефон", self._connect_calls), "quiet"), False, False, 0)
        audio.pack_start(row, False, False, 0)
        self.gateway_selector = Gtk.ComboBoxText()
        self.gateway_selector.set_no_show_all(True)
        self.gateway_selector.connect("changed", lambda *_: self._update_dialpad())
        audio.pack_start(self.gateway_selector, False, False, 0)
        self.audio_expander.add(audio)
        page.pack_start(self.audio_expander, False, False, 0)
        return page

    def _files_page(self):
        page = self._page("Файлы", "")
        drop = box(cls="card")
        style(drop, "drop-zone")
        drop.set_size_request(-1, 210)
        drop.pack_start(Gtk.Image.new_from_icon_name("folder-download-symbolic", Gtk.IconSize.DIALOG), True, False, 0)
        title = label("Перетащи файлы сюда", "section")
        title.set_xalign(0.5)
        drop.pack_start(title, False, False, 0)
        choose = button("Выбрать файлы", self._choose_files, True)
        choose.set_halign(Gtk.Align.CENTER)
        drop.pack_start(choose, True, False, 0)
        drop.drag_dest_set(Gtk.DestDefaults.ALL, [], Gdk.DragAction.COPY)
        drop.drag_dest_add_uri_targets()
        drop.connect("drag-data-received", self._files_dropped)
        page.pack_start(drop, False, False, 0)
        received = box(False, 12, "group-row")
        received.pack_start(label("Файлы с телефона"), True, True, 0)
        received.pack_end(style(button("Открыть загрузки", self._open_downloads), "quiet"), False, False, 0)
        page.pack_start(style(received, "group"), False, False, 0)
        links = box(spacing=8, cls="card")
        links.pack_start(label("Ссылка на телефон"), False, False, 0)
        link_row = box(False, 8)
        self.url_entry = entry("https://…")
        self.url_entry.connect("activate", lambda *_: self._send_url())
        link_row.pack_start(self.url_entry, True, True, 0)
        link_row.pack_end(button("Открыть", self._send_url), False, False, 0)
        links.pack_start(link_row, False, False, 0)
        page.pack_start(links, False, False, 0)
        page.pack_start(label("Прогресс передачи — в уведомлениях Ubuntu. Папку сначала упакуй в ZIP.", "small", True), False, False, 0)
        return page

    def _screen_page(self):
        page = self._page("Экран телефона", "")
        main = box(cls="card")
        main.pack_start(Gtk.Image.new_from_icon_name("phone-symbolic", Gtk.IconSize.DIALOG), False, False, 8)
        self.native_screen_status = label("Открой экран и подтверди показ на телефоне.", "muted", True)
        main.pack_start(self.native_screen_status, False, False, 0)
        row = box(False)
        self.native_screen_button = button("Открыть экран телефона", self._request_native_screen, True)
        row.pack_start(self.native_screen_button, False, False, 0)
        self.native_stop_button = style(button("Завершить показ", self._stop_native_screen), "quiet")
        self.native_stop_button.set_no_show_all(True)
        row.pack_start(self.native_stop_button, False, False, 0)
        main.pack_start(row, False, False, 6)
        page.pack_start(main, False, False, 0)
        page.pack_start(label("Для управления мышью разреши управление в этом сеансе на телефоне. При блокировке телефона показ останавливается.", "muted", True), False, False, 0)
        help_panel = Gtk.Expander(label="Экран не появился?")
        help_box = box()
        help_box.pack_start(label("Убедись, что на телефоне установлена новая версия VELIQEN Link, и нажми «Показать экран» в ней. Если файлы передаются, а экран не открывается, разреши подключение ниже.", "muted", True), False, False, 0)
        help_box.pack_start(label("Это разрешит приём экрана только от выбранного телефона. Ubuntu может запросить пароль администратора.", "small", True), False, False, 0)
        self.network_screen_button = button("Разрешить подключение экрана", self._allow_screen_network)
        help_box.pack_start(self.network_screen_button, False, False, 0)
        help_panel.add(help_box)
        page.pack_start(help_panel, False, False, 0)
        extra = Gtk.Expander(label="Дополнительно: USB и scrcpy")
        extra.add(self._adb_screen_options())
        extra.connect("notify::expanded", lambda w, _p: self._refresh_adb() if w.get_expanded() else None)
        page.pack_start(extra, False, False, 0)
        return page

    def _adb_screen_options(self):
        page = box()
        launch = box(cls="card")
        self.screen_status = label("Для первого подключения включи «Беспроводную отладку» на телефоне.", "muted", True)
        launch.pack_start(self.screen_status, False, False, 0)
        self.adb_selector = Gtk.ComboBoxText()
        launch.pack_start(self.adb_selector, False, False, 0)
        self.screen_off = Gtk.CheckButton(label="Погасить физический экран телефона при трансляции")
        launch.pack_start(self.screen_off, False, False, 0)
        row = box(False)
        row.pack_start(button("Открыть экран", self._launch_screen, True), False, False, 0)
        row.pack_start(button("Остановить", lambda: self.work(self.screen.stop)), False, False, 0)
        row.pack_start(button("Обновить список", self._refresh_adb), False, False, 0)
        launch.pack_start(row, False, False, 0)
        page.pack_start(launch, False, False, 0)
        pair = box(cls="card")
        pair.pack_start(label("Первое сопряжение", "section"), False, False, 0)
        pair.pack_start(label("На телефоне: Для разработчиков → Беспроводная отладка → Сопряжение с помощью кода.", "muted", True), False, False, 0)
        self.pair_endpoint = entry("IP и порт сопряжения, например 192.168.1.10:37123")
        self.pair_code = entry("Код с телефона", secret=True)
        pair.pack_start(self.pair_endpoint, False, False, 0)
        pair.pack_start(self.pair_code, False, False, 0)
        pair.pack_start(button("Сопрячь для экрана", self._pair_adb), False, False, 0)
        page.pack_start(pair, False, False, 0)
        connect = box(cls="card")
        connect.pack_start(label("Подключение после сопряжения", "section"), False, False, 0)
        connect.pack_start(label("IP и порт на основном экране беспроводной отладки отличаются от порта сопряжения.", "muted", True), False, False, 0)
        self.connect_endpoint = entry("IP и порт подключения")
        connect.pack_start(self.connect_endpoint, False, False, 0)
        connect.pack_start(button("Подключить", self._connect_adb), False, False, 0)
        page.pack_start(connect, False, False, 0)
        return page


    def _settings_page(self):
        page = self._page("Настройки", "")
        general = box(spacing=0, cls="group")
        appearance = box(False, 12, "group-row")
        appearance.pack_start(label("Оформление"), True, True, 0)
        self.appearance_selector = Gtk.ComboBoxText()
        for key, title in [("system", "Как в системе"), ("light", "Светлое"), ("dark", "Тёмное")]:
            self.appearance_selector.append(key, title)
        self.appearance_selector.set_active_id(self.config.get("appearance") or "system")
        self.appearance_selector.connect("changed", lambda w: (self._save(appearance=w.get_active_id()), self._apply_appearance()))
        appearance.pack_end(self.appearance_selector, False, False, 0)
        general.pack_start(appearance, False, False, 0)
        keep = Gtk.CheckButton(label="Работать после закрытия окна")
        keep.set_active(self.config.get("close_to_background"))
        keep.connect("toggled", lambda w: self._save(close_to_background=w.get_active()))
        general.pack_start(style(keep, "group-row"), False, False, 0)
        self.autostart_toggle = Gtk.CheckButton(label="Запускать при входе в Ubuntu")
        self.autostart_toggle.set_active(autostart_enabled())
        self.autostart_toggle.connect("toggled", self._toggle_autostart)
        general.pack_start(style(self.autostart_toggle, "group-row"), False, False, 0)
        page.pack_start(general, False, False, 0)
        card = box(spacing=8, cls="card")
        self.clipboard_toggle = Gtk.CheckButton(label="Передавать буфер с Ubuntu на телефон")
        self.clipboard_toggle.set_active(self.config.get("clipboard_sync"))
        self.clipboard_toggle.connect("toggled", self._toggle_clipboard)
        card.pack_start(self.clipboard_toggle, False, False, 0)
        self.clipboard_status = label("", "small", True)
        card.pack_start(self.clipboard_status, False, False, 0)
        self.clipboard_extension_button = button("Включить дополнение GNOME", self._enable_clipboard_extension)
        self.clipboard_extension_button.set_no_show_all(True)
        card.pack_start(self.clipboard_extension_button, False, False, 0)
        details = Gtk.Expander(label="Как работает буфер")
        details.add(label("Это автопередача из VELIQEN Link. У KDE Connect есть отдельная настройка буфера в параметрах связи. Для полного отключения проверь обе. История буфера здесь не сохраняется. Дополнение GNOME входит в пакет; после первой установки может понадобиться повторный вход в Ubuntu.", "small", True))
        card.pack_start(details, False, False, 0)
        page.pack_start(card, False, False, 0)
        extra = Gtk.Expander(label="Подключение и диагностика")
        more = box(spacing=8)
        for title, action in [("Выбрать телефон", self._pair_dialog), ("Параметры службы связи", self._open_kde_config),
                              ("Удалить сопряжение", self._unpair), ("Проверить подключение", self._show_doctor)]:
            more.pack_start(button(title, action), False, False, 0)
        extra.add(more)
        page.pack_start(extra, False, False, 0)
        about = box(False, 8)
        about.pack_start(label("VELIQEN Link " + __version__, "small"), True, True, 0)
        about.pack_end(style(button("О приложении", self._about), "quiet"), False, False, 0)
        about.pack_end(style(button("Завершить", self.exit_application), "quiet"), False, False, 0)
        page.pack_end(about, False, False, 0)
        return page

    def select_page(self, name):
        if self._selecting_page:
            return
        self._selecting_page = True
        self.stack.set_visible_child_name(name)
        for key, nav in self.nav.items():
            nav.set_active(key == name)
        self._selecting_page = False
        self._sync_call_banner()
        if name == "settings":
            self._check_clipboard()
        elif name == "calls":
            self._refresh_audio()
        elif name == "messages" and self.current_device:
            if any(d.get("id") == self.current_device and d.get("reachable") and d.get("paired") for d in self.devices):
                self._load_conversations()

    def _save(self, **values):
        try:
            self.config.set(**values)
        except OSError:
            self.say("Не удалось сохранить настройки в домашней папке.", True)

    def _sync_call_banner(self):
        if not hasattr(self, "call_banner"):
            return
        address = self.config.get("bluetooth_devices").get(self.current_device)
        calls = [c for c in self.calls_state.get("calls", [])
                 if address and c.get("address") == address and c.get("state") != "disconnected"]
        visible = bool(calls) and self.stack.get_visible_child_name() != "calls"
        self.call_banner.set_reveal_child(visible)
        if not visible:
            return
        clear(self.call_banner_box)
        call = next((c for c in calls if c.get("state") == "incoming"), calls[0])
        titles = {"incoming": "Входящий", "active": "Разговор", "held": "На удержании",
                  "dialing": "Набираем", "alerting": "Вызов", "waiting": "Ожидание"}
        name = call.get("name") or call.get("number") or "Номер скрыт"
        title = label(titles.get(call.get("state"), "Звонок") + " · " + name)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        self.call_banner_box.pack_start(title, True, True, 0)
        self.call_banner_box.pack_start(style(button("Открыть", lambda: self.select_page("calls")), "quiet"), False, False, 0)
        if call.get("state") == "incoming":
            self.call_banner_box.pack_start(button("Принять", lambda p=call["path"]: self._answer(p), True), False, False, 0)
        self.call_banner_box.pack_end(style(button("Отклонить" if call.get("state") == "incoming" else "Завершить",
            lambda p=call["path"]: self._hangup(p)), "danger"), False, False, 0)
        self.call_banner_box.show_all()

    def _select_device(self, device_id):
        if not device_id:
            return False
        if device_id == self.current_device:
            return True
        if getattr(self, "_selecting_device", False):
            return False
        self._selecting_device = True
        try:
            if not self._confirm_new_message():
                return False
            self.current_device = device_id
            self._save(device_id=device_id)
            self.selected_thread = None
            self.message_device_loaded = None
            clear(self.threads)
            self.sms_number.set_text("")
            self.sms_sim.set_text("")
            self.sms_body.get_buffer().set_text("")
            self.message_view.get_buffer().set_text("Нажми «Обновить переписку».")
            self._render_devices()
            self._render_calls()
            return True
        finally:
            self._selecting_device = False

    def _device_changed(self, selector):
        if getattr(self, "_updating_devices", False):
            return
        selected = selector.get_active_id()
        if selected and not self._select_device(selected):
            self._updating_devices = True
            try:
                selector.set_active_id(self.current_device)
            finally:
                self._updating_devices = False

    def _device(self):
        for device in self.devices:
            if device.get("id") == self.current_device and device.get("paired") and device.get("reachable"):
                return device["id"]
        self.say("Сначала выбери сопряжённый телефон, доступный в сети.", True)
        return None

    def device_action(self, operation, success=None):
        device = self._device()
        if device:
            self.work(lambda: operation(device), lambda _: self.say(success or "Команда передана телефону."))

    def _poll(self):
        if self.closing:
            return False
        self.refresh()
        return True

    def refresh(self):
        if self.refresh_pending or self.closing:
            return
        self.refresh_pending = True
        def query():
            value = {"devices": [], "battery": {}, "battery_device": "", "kde_error": ""}
            try:
                value["devices"] = self.kde.devices()
                selected = self.current_device
                if selected and any(d["id"] == selected and d["reachable"] and d["paired"] for d in value["devices"]):
                    value["battery"] = self.kde.battery(selected)
                    value["battery_device"] = selected
            except Exception as exc:
                value["kde_error"] = str(exc)
            try:
                value["calls"] = self.telephony.refresh()
            except Exception as exc:
                value["calls"] = {"available": False, "status": str(exc), "calls": [], "gateways": []}
            return value
        self.work(query, self._refreshed, busy=False)

    def _refreshed(self, value):
        self.refresh_pending = False
        self.devices = value["devices"]
        paired = [d for d in self.devices if d.get("paired")]
        if not self.current_device and paired:
            self.current_device = next((d["id"] for d in paired if d.get("reachable")), paired[0]["id"])
            self._save(device_id=self.current_device)
        self._updating_devices = True
        self.device_selector.remove_all()
        if not paired:
            self.device_selector.append("", "Нет подключённых устройств")
            self.device_selector.set_active(0)
        else:
            for device in paired:
                mark = "●" if device.get("reachable") else "○"
                self.device_selector.append(device["id"], f"{mark}  {device.get('name', 'Телефон')}")
            self.device_selector.set_active_id(self.current_device)
        self._updating_devices = False
        self.device_selector.set_visible(len(paired) > 1)
        self._render_devices()
        battery = value["battery"] if value.get("battery_device") == self.current_device else {}
        charge = battery.get("charge")
        self.battery_label.set_text(f"{charge}%" + (" · заряжается" if battery.get("charging") else "") if charge is not None and charge >= 0 else "")
        self.calls_state = value["calls"]
        self._render_calls()
        self._maybe_auto_audio()
        self.work(lambda: self.audio.reconcile_calls(value["calls"]), busy=False)
        if (hasattr(self, "stack") and self.stack.get_visible_child_name() == "messages"
                and self.message_device_loaded != self.current_device
                and any(d.get("id") == self.current_device and d.get("paired") and d.get("reachable") for d in self.devices)):
            self._load_conversations()
        if value["kde_error"] and not self.devices:
            self.phone_status.set_text("Служба связи ещё не запущена. Нажми «Подключить телефон». Если поиск не начнётся, повтори установку приложения.")

    def _render_devices(self):
        selected = next((d for d in self.devices if d["id"] == self.current_device and d.get("paired")), None)
        online = bool(selected and selected.get("reachable"))
        if self.native_request_pending and self.native_request_device != self.current_device:
            self.native_request_pending = False
            self.native_request_sequence += 1
        if selected:
            name = selected.get("name", "Телефон")
            self.phone_title.set_text(name)
            self.header_device.set_text(name)
            self.phone_status.set_text("Подключён. Можно передавать файлы, открыть экран или написать сообщение." if online else
                "Связь сохранена. Открой приложение на телефоне и подключи его к той же сети Wi-Fi.")
        else:
            self.phone_title.set_text("Подключим телефон")
            self.header_device.set_text("Телефон не подключён")
            self.phone_status.set_text("Открой VELIQEN Link на телефоне. Оба устройства должны быть в одной сети Wi-Fi.")
        self.connection_label.set_text("Подключён по Wi-Fi" if online else "Нет связи")
        self.header_state.set_text("Подключён" if online else "Не в сети" if selected else "Подключить")
        self.home_primary.set_label("Открыть экран телефона" if online else "Найти телефон" if selected else "Подключить телефон")
        incoming = next((d for d in self.devices if d.get("pair_requested_by_peer") and d.get("reachable")), None)
        if not selected and incoming:
            self.phone_title.set_text((incoming.get("name") or "Телефон") + " хочет подключиться")
            self.phone_status.set_text("Нажми «Проверить запрос», сравни код на обоих устройствах и подтверди подключение.")
            self.home_primary.set_label("Проверить запрос")
        for widget, visible in [(self.home_intro, not online), (self.home_welcome, not online), (self.home_actions, online),
                                (self.home_clipboard, online), (self.home_details, online),
                                (self.home_secondary, bool(selected))]:
            reveal(widget, visible)
        self._check_clipboard()
        reveal(self.native_stop_button, bool(self.native_session))
        self.native_screen_button.set_sensitive(online and not self.native_request_pending)
        if not online:
            self.native_screen_status.set_text("Сначала подключи телефон на главной странице.")
        allowed = [self.current_device] if online else []
        self.receiver.set_allowed_devices(allowed)
        if online:
            try:
                self.receiver.start()
            except Exception as exc:
                self.native_screen_status.set_text(str(exc))
        if self.native_session and self.native_session.get("device_id") not in allowed:
            self._native_session_ended("Выбран другой телефон или соединение потеряно.")
        connection_state = (self.current_device, online, incoming.get("id") if incoming else None)
        if connection_state != self._last_connection_state:
            self._last_connection_state = connection_state
            if online:
                self.say("Телефон подключён. Выбери действие на главной.")
            elif incoming:
                self.say("Телефон отправил запрос. Нажми «Проверить запрос».")
            elif selected:
                self.say("Ждём телефон в той же сети Wi-Fi. Сопряжение сохранено.")

    def _pair_dialog(self):
        if self.pair_dialog and self.pair_dialog.get_visible():
            self.pair_dialog.present()
            return
        dialog = Gtk.Dialog(title="Подключить телефон", transient_for=self.window, modal=True)
        self.pair_dialog = dialog
        dialog.set_default_size(560, 420)
        dialog.add_button("Закрыть", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_border_width(24)
        content.set_spacing(16)
        content.pack_start(label("Выбери свой телефон", "heading"), False, False, 0)
        instruction = label("Открой VELIQEN Link на телефоне и подключи оба устройства к одному Wi-Fi.", "muted", True)
        content.pack_start(instruction, False, False, 0)
        devices_box = box()
        content.pack_start(devices_box, True, True, 0)
        progress = {"loading": False, "target": None, "closed": False, "selecting": False}
        def requested(device):
            if progress["closed"] or progress["selecting"]:
                return
            progress["target"] = device["id"]
            instruction.set_text("Проверь имя компьютера и подтверди запрос на телефоне. Это нужно только при первом подключении.")
            if device.get("verification_key"):
                instruction.set_text("Сравни код на обоих устройствах: " + str(device["verification_key"]) + ". Подтверди только если код совпадает.")
            self.work(lambda: self.kde.pair(device["id"]), lambda _: poll(),
                      "Ждём подтверждение на телефоне…", on_error=lambda text: instruction.set_text(text) if not progress["closed"] else None)
        def choose(device):
            if progress["closed"] or progress["selecting"]:
                return False
            progress["selecting"] = True
            progress["target"] = None
            try:
                if not self._select_device(device["id"]):
                    return False
                dialog.response(Gtk.ResponseType.CLOSE)
                return True
            finally:
                progress["selecting"] = False
        def draw(devices):
            progress["loading"] = False
            if progress["closed"] or progress["selecting"]:
                return
            if progress["target"]:
                ready = next((d for d in devices if d["id"] == progress["target"] and d.get("paired")), None)
                if ready and choose(ready):
                    return
            clear(devices_box)
            visible = [d for d in devices if d.get("reachable")]
            if not visible:
                devices_box.pack_start(label("Ищем телефон…", "section"), False, False, 0)
                devices_box.pack_start(label("Пока список пуст, проверь, что приложение на телефоне открыто. Гостевая сеть может запрещать связь устройств.", "muted", True), False, False, 0)
            for device in visible:
                row = box(False, 12, "card")
                details = box(spacing=5)
                details.pack_start(label(device.get("name", "Телефон"), "section", True), False, False, 0)
                status = "Уже подключён" if device.get("paired") else "Хочет подключиться" if device.get("pair_requested_by_peer") else "Найден в этой сети"
                details.pack_start(label(status, "muted"), False, False, 0)
                if device.get("pair_requested_by_peer") and device.get("verification_key"):
                    details.pack_start(label("Код: " + str(device["verification_key"]), "accent-text", True), False, False, 0)
                row.pack_start(details, True, True, 0)
                action = button("Выбрать" if device.get("paired") else "Подтвердить" if device.get("pair_requested_by_peer") else "Подключить",
                                lambda d=device: choose(d) if d.get("paired") else requested(d), True)
                action.set_sensitive(progress["target"] != device["id"])
                row.pack_end(action, False, False, 0)
                devices_box.pack_start(row, False, False, 0)
            devices_box.show_all()
        def failed(text):
            progress["loading"] = False
            if not progress["closed"]:
                instruction.set_text(text)
        def poll():
            if progress["closed"]:
                return False
            if progress["selecting"]:
                return True
            if not progress["loading"]:
                progress["loading"] = True
                self.work(self._discover, draw, busy=False, on_error=failed)
            return True
        content.pack_start(style(button("Открыть настройки сети", self._network_settings), "quiet"), False, False, 0)
        dialog.show_all()
        poll()
        timer = GLib.timeout_add_seconds(2, poll)
        dialog.run()
        progress["closed"] = True
        GLib.source_remove(timer)
        dialog.destroy()
        self.pair_dialog = None
        self.refresh()

    def _network_settings(self):
        for command in [("gnome-control-center", "wifi"), ("nm-connection-editor",)]:
            if shutil.which(command[0]):
                subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return
        self.say("Открой настройки Wi-Fi и подключи оба устройства к одной сети.")

    def _discover(self):
        self.kde.start_service()
        try:
            self.kde.refresh()
        except Exception:
            pass
        return self.kde.devices()

    def _open_kde_config(self):
        self.work(lambda: self.kde.open_config())

    def _paste_text(self):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).request_text(
            lambda clipboard, text, data: self.clipboard_entry.set_text(text or ""), None)

    def _send_text(self):
        text = self.clipboard_entry.get_text()
        if text:
            self.device_action(lambda d: self.kde.send_clipboard(d, text), "Текст передан в буфер телефона.")

    def _clipboard_changed(self, text):
        if not self.current_device:
            return
        if not any(d["id"] == self.current_device and d.get("reachable") and d.get("paired") for d in self.devices):
            return
        if self.clipboard_pending:
            self.clipboard_queued = (self.current_device, text)
            return
        self.clipboard_pending = True
        device = self.current_device
        def send():
            try:
                self.kde.send_clipboard(device, text)
            finally:
                GLib.idle_add(self._clipboard_finished)
        self.work(send, busy=False)

    def _clipboard_finished(self):
        self.clipboard_pending = False
        queued, self.clipboard_queued = self.clipboard_queued, None
        if queued and queued[0] == self.current_device and self.config.get("clipboard_sync"):
            self._clipboard_changed(queued[1])
        return False

    def _toggle_clipboard(self, widget):
        enabled = widget.get_active()
        self._save(clipboard_sync=enabled)
        if self.clipboard_bridge:
            self.clipboard_bridge.set_enabled(enabled)
            if enabled and not self.clipboard_bridge.x11:
                self._enable_clipboard_extension()
        self._check_clipboard()

    def _toggle_autostart(self, widget):
        if getattr(self, "_autostart_resetting", False):
            return
        try:
            set_autostart(widget.get_active())
        except (OSError, RuntimeError) as exc:
            self.say(str(exc), True)
            self._autostart_resetting = True
            try:
                widget.set_active(autostart_enabled())
            finally:
                self._autostart_resetting = False

    def _enable_clipboard_extension(self):
        if self._extension_enable_pending:
            return
        self._extension_enable_pending = True
        self.clipboard_extension_button.set_sensitive(False)
        def done(message):
            self._extension_enable_pending = False
            self.clipboard_extension_button.set_sensitive(True)
            self.say(message)
            self._check_clipboard()
        def failed(message):
            self._extension_enable_pending = False
            self.clipboard_extension_button.set_sensitive(True)
            self.say(message, True)
            self._check_clipboard()
        self.work(enable_clipboard_extension, done, "Включаем дополнение буфера…", on_error=failed)

    def _check_clipboard(self):
        if not hasattr(self, "clipboard_status") or getattr(self, "_clipboard_check_pending", False):
            return
        self._clipboard_check_pending = True
        def loaded(available):
            self._clipboard_check_pending = False
            enabled = self.config.get("clipboard_sync")
            self.clipboard_status.set_text("Автопередача включена." if available and enabled else
                "Выключена. Кнопка «Буфер» передаёт текст вручную." if available else
                "Для автопередачи включи дополнение GNOME. После первой установки выйди из сеанса и войди снова. Кнопка «Буфер» передаёт текст вручную.")
            if hasattr(self, "clipboard_extension_button"):
                reveal(self.clipboard_extension_button, not available and "GNOME" in os.environ.get("XDG_CURRENT_DESKTOP", "").upper())
            if hasattr(self, "home_clipboard_status"):
                self.home_clipboard_status.set_text("Включена" if enabled and available else "Нужно включить расширение" if enabled else "Выключена · можно передавать вручную")
        if self.clipboard_bridge is None:
            loaded(False)
        else:
            self.work(self.clipboard_bridge.available, loaded, busy=False, on_error=lambda _text: loaded(False))

    def _load_conversations(self):
        if self._conversation_pending:
            return
        device = self._device()
        if not device:
            return
        self._conversation_pending = True
        def loaded(items):
            self._conversation_pending = False
            if self.current_device != device:
                return
            self.message_device_loaded = device
            self._loading_threads = True
            clear(self.threads)
            selected_row = None
            for item in items:
                row = Gtk.ListBoxRow()
                row.thread = item
                content = box(spacing=6)
                content.set_border_width(10)
                addresses = item.get("addresses") or []
                title = ", ".join(str(a) for a in addresses) or "Сообщение"
                row.search_text = (title + " " + str(item.get("body") or "")).casefold()
                title_label = label(title, "section")
                title_label.set_ellipsize(Pango.EllipsizeMode.END)
                title_label.set_max_width_chars(20)
                content.pack_start(title_label, False, False, 0)
                preview = label((item.get("body") or "")[:80], "small", True)
                preview.set_max_width_chars(22)
                preview.set_lines(2)
                preview.set_ellipsize(Pango.EllipsizeMode.END)
                content.pack_start(preview, False, False, 0)
                row.add(content)
                self.threads.add(row)
                if self.selected_thread is not None and self.selected_thread == item.get("thread_id", item.get("threadID", item.get("id"))):
                    self.threads.select_row(row)
                    selected_row = row
            self._loading_threads = False
            self.threads.show_all()
            if selected_row is not None:
                addresses = selected_row.thread.get("addresses") or []
                recipient = str(addresses[0]) if len(addresses) == 1 else ""
                if recipient == self.sms_number.get_text().strip():
                    self._thread_selected(self.threads, selected_row)
            if not items:
                self.say("Переписок пока нет. Проверь доступ к SMS на телефоне и обнови список.")
        def failed(text):
            self._conversation_pending = False
            if self.current_device == device:
                self.say(text, True)
        self.work(lambda: self.kde.conversations(device), loaded, busy=False, on_error=failed)

    def _thread_selected(self, _, row):
        if row is None or getattr(self, "_loading_threads", False):
            return
        device = self._device()
        if not device:
            return
        thread = row.thread
        thread_id = thread.get("thread_id", thread.get("threadID", thread.get("id")))
        addresses = thread.get("addresses") or []
        recipient = str(addresses[0]) if len(addresses) == 1 else ""
        if recipient != self.sms_number.get_text().strip():
            if not self._confirm_new_message():
                self._loading_threads = True
                previous = next((r for r in self.threads.get_children() if r.thread.get("thread_id", r.thread.get("threadID", r.thread.get("id"))) == self.selected_thread), None)
                self.threads.select_row(previous)
                self._loading_threads = False
                return
            self.sms_body.get_buffer().set_text("")
        self.selected_thread = thread_id
        self.sms_number.set_text(recipient)
        self.message_view.get_buffer().set_text("Загружаем сообщения…")
        def loaded(items):
            if self.selected_thread != thread_id or self.current_device != device:
                return
            if items:
                self.message_view.show_messages(items)
            else:
                self.message_view.get_buffer().set_text("Сообщения ещё загружаются. Выбери переписку повторно через несколько секунд.")
        self.work(lambda: self.kde.messages(device, thread_id), loaded, busy=False,
            on_error=lambda text: self.say(text, True) if self.selected_thread == thread_id and self.current_device == device else None)

    def _send_sms(self):
        if self.sms_pending:
            return
        device = self._device()
        if not device:
            return
        number = self.sms_number.get_text().strip()
        buffer = self.sms_body.get_buffer()
        text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        if not number or not text.strip():
            self.say("Укажи получателя и напиши сообщение.", True)
            return
        raw_sim = self.sms_sim.get_text().strip()
        try:
            sim = int(raw_sim) if raw_sim else -1
        except ValueError:
            self.say("ID SIM должен быть числом, либо оставь поле пустым.", True)
            return
        self.sms_pending = True
        self.sms_send_button.set_sensitive(False)
        def reset():
            self.sms_pending = False
            self.sms_send_button.set_sensitive(True)
            return False
        def send():
            try:
                return self.kde.send_sms(device, number, text, sim)
            except Exception:
                GLib.idle_add(reset)
                raise
        def requested(_):
            reset()
            current_text = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
            if self.current_device == device and self.sms_number.get_text().strip() == number and current_text == text:
                buffer.set_text("")
            self.say("Запрос отправки передан телефону. Проверь итоговый статус в переписке.")
            if self.current_device == device:
                GLib.timeout_add_seconds(2, lambda: (self._load_conversations(), False)[1])
        self.work(send, requested, "Передаём SMS телефону…")

    def _render_calls(self):
        state = self.calls_state
        remembered = self.config.get("bluetooth_devices").get(self.current_device)
        gateways = [g for g in state.get("gateways", []) if remembered and g.get("address") == remembered]
        phone = next((d for d in self.devices if d.get("id") == self.current_device), {})
        self.telephony_status.set_text("Звонки готовы · Bluetooth" if gateways else "Подключи звонки")
        previous = self.gateway_selector.get_active_id()
        self._updating_call_gateway = True
        self.gateway_selector.remove_all()
        for gateway in gateways:
            text = gateway.get("name") or "Телефон для звонков"
            self.gateway_selector.append(gateway["path"], text)
        if previous:
            self.gateway_selector.set_active_id(previous)
        if self.gateway_selector.get_active() < 0 and gateways:
            matching = next((g["path"] for g in gateways if remembered and g.get("address") == remembered), None)
            if matching:
                self.gateway_selector.set_active_id(matching)
            elif len(gateways) == 1:
                self.gateway_selector.set_active(0)
        self._updating_call_gateway = False
        reveal(self.gateway_selector, len(gateways) > 1)
        self.call_label.set_text("Звонки готовы" if gateways else "Звонки: подключить Bluetooth")
        reveal(self.connect_calls_button, not gateways)
        self.connect_calls_button.set_sensitive(not self.bluetooth_pending)
        reveal(self.call_system_button, bool(remembered) and not gateways and not self.bluetooth_pending)
        if gateways:
            self.call_setup_hint.set_text("Можно звонить и принимать вызовы.")
        elif not self.current_device:
            self.call_setup_hint.set_text("Сначала подключи телефон на главной странице.")
        elif not remembered and not self.bluetooth_pending:
            self.call_setup_hint.set_text("Подключи Bluetooth один раз — потом всё будет готово автоматически.")
        self.call_setup_hint.set_no_show_all(True)
        reveal(self.call_setup_hint, not gateways)
        self._update_dialpad()
        clear(self.calls_box)
        calls = state.get("calls", [])
        titles = {"incoming": "Входящий звонок", "waiting": "Ожидание", "active": "Разговор",
                  "held": "Удержание", "dialing": "Набираем номер", "alerting": "Вызов"}
        for call in calls:
            if call.get("state") == "disconnected" or not remembered or call.get("address") != remembered:
                continue
            card = box(False, 12, "card")
            details = box(spacing=3)
            details.pack_start(label(titles.get(call.get("state"), "Звонок"), "muted"), False, False, 0)
            identity = label(call.get("name") or call.get("number") or "Номер скрыт", "section")
            identity.set_ellipsize(Pango.EllipsizeMode.END)
            identity.set_max_width_chars(30)
            details.pack_start(identity, False, False, 0)
            card.pack_start(details, True, True, 0)
            actions = box(False)
            if call.get("state") == "incoming":
                actions.pack_start(button("Принять", lambda p=call["path"]: self._answer(p), True), False, False, 0)
            actions.pack_start(style(button("Отклонить" if call.get("state") == "incoming" else "Завершить", lambda p=call["path"]: self._hangup(p)), "danger"), False, False, 0)
            card.pack_start(actions, False, False, 0)
            self.calls_box.pack_start(card, False, False, 0)
        self.calls_box.show_all()
        incoming = {c["path"]: c for c in calls
                    if c.get("state") == "incoming" and remembered and c.get("address") == remembered}
        for path in set(self.incoming_notifications) - set(incoming):
            self.withdraw_notification(self.incoming_notifications.pop(path))
        for path, call in incoming.items():
            if path in self.incoming_notifications:
                continue
            identifier = "call-" + hashlib.sha256(path.encode()).hexdigest()[:16]
            notification = Gio.Notification.new("Входящий звонок")
            notification.set_body(call.get("name") or call.get("number") or "Номер скрыт")
            notification.set_priority(Gio.NotificationPriority.URGENT)
            notification.set_default_action("app.show")
            notification.add_button_with_target("Принять", "app.answer", GLib.Variant("s", path))
            notification.add_button_with_target("Отклонить", "app.hangup", GLib.Variant("s", path))
            self.send_notification(identifier, notification)
            self.incoming_notifications[path] = identifier
        if hasattr(self, "_sync_call_banner"):
            self._sync_call_banner()

    def _update_dialpad(self):
        if not hasattr(self, "dialpad") or getattr(self, "_updating_call_gateway", False):
            return
        remembered = self.config.get("bluetooth_devices").get(self.current_device)
        selected = self.gateway_selector.get_active_id()
        gateway = next((g for g in self.calls_state.get("gateways", [])
                        if g["path"] == selected and remembered and g.get("address") == remembered), None)
        calls = self.calls_state.get("calls", [])
        target = active_call(calls, gateway)
        live = any(c.get("address") == remembered and c.get("state") != "disconnected" for c in calls) if remembered else False
        mode = "tones" if target else "blocked" if live else "dial"
        key = call_key(target)
        if self.dialpad.call_key != key:
            self._tone_queue.clear()
        self.dialpad.set_state(mode, ready=bool(gateway), pending=self.dial_pending, call_key=key)

    def _gateway(self):
        selected = self.gateway_selector.get_active_id()
        remembered = self.config.get("bluetooth_devices").get(self.current_device)
        gateway = next((g for g in self.calls_state.get("gateways", [])
                        if g["path"] == selected and remembered and g.get("address") == remembered), None)
        if gateway is None:
            self.say("Нажми «Подключить звонки», чтобы связать Bluetooth с выбранным телефоном.", True)
        return gateway

    def _dial(self):
        if self.dial_pending:
            return
        gateway = self._gateway()
        number = self.dial_number.get_text().strip()
        if not gateway or not number:
            return
        if any(c.get("state") != "disconnected" and
               (c.get("gateway_path") == gateway["path"] or
                (gateway.get("address") and c.get("address") == gateway["address"]))
               for c in getattr(self, "calls_state", {}).get("calls", [])):
            self.say("Сначала заверши текущий звонок. Цифры на клавиатуре отправляются в текущий разговор.", True)
            return
        try:
            number = normalize_number(number)
        except TelephonyError as exc:
            self.say(str(exc), True)
            return
        device_id = getattr(self, "current_device", None)
        self.dial_pending = True
        self.dial_button.set_sensitive(False)
        if hasattr(self, "dialpad"):
            self._update_dialpad()
        def reset():
            self.dial_pending = False
            if hasattr(self, "dialpad"):
                self._update_dialpad()
            else:
                self.dial_button.set_sensitive(True)
            return False
        def place():
            if device_id is not None and (self.current_device != device_id or
                    self.config.get("bluetooth_devices").get(device_id) != gateway.get("address")):
                raise TelephonyError("Выбран другой телефон. Номер не набран.")
            return self.telephony.dial(gateway["path"], number)
        def requested(_):
            self.refresh()
            GLib.timeout_add_seconds(3, reset)
        def failed(_):
            reset()
        self.work(place, requested,
                  "Набираем номер через телефон…", on_error=failed)

    def _pick_number(self, number):
        self.select_page("calls")
        self._update_dialpad()
        if self.dial_pending or not self.dialpad.set_number(str(number or "")):
            self.say("Сначала заверши текущий звонок. Новый номер пока не набран.")
            return False
        return True

    def _send_tone(self, digit, expected_key):
        if not isinstance(digit, str) or len(digit) != 1 or digit not in "0123456789*#":
            return False
        gateway = self._gateway()
        target = active_call(self.calls_state.get("calls", []), gateway)
        if not target or call_key(target) != expected_key:
            self.say("Текущий звонок изменился. Цифра не отправлена.", True)
            return False
        if len(self._tone_queue) >= 40:
            self.say("Подожди, пока телефон примет набранные цифры.")
            return False
        self._tone_queue.append((self.current_device, dict(target), digit))
        self._drain_tones()
        return True

    def _drain_tones(self):
        if self._tone_pending or not self._tone_queue:
            return
        device_id, target, digit = self._tone_queue.pop(0)
        self._tone_pending = True
        def send():
            if self.current_device != device_id or self.config.get("bluetooth_devices").get(device_id) != target.get("address"):
                raise TelephonyError("Выбран другой телефон. Цифры не отправлены.")
            return self.telephony.send_call_tones(target["path"], digit,
                                                 expected_address=target["address"],
                                                 expected_number=target.get("number", ""))
        def done(_):
            self._tone_pending = False
            self._drain_tones()
        def failed(text):
            self._tone_pending = False
            self._tone_queue.clear()
            self.say(text, True)
            self.refresh()
        self.work(send, done, busy=False, on_error=failed)

    def _answer(self, path):
        self.work(lambda: self.telephony.answer(path), lambda _: (self.refresh(), self._refresh_audio()), "Принимаем звонок…")

    def _hangup(self, path):
        def finish():
            self.telephony.hangup(path)
            self.audio.reconcile_calls(self.telephony.refresh())
        self.work(finish, lambda _: self.refresh(), "Завершаем звонок…")

    def _bluetooth_settings(self):
        result = self.bluetooth.open_settings()
        self.say(result["message"])

    def _refresh_audio(self):
        if not hasattr(self, "microphone_selector"):
            return
        gateway = next((g for g in self.calls_state.get("gateways", []) if g["path"] == self.gateway_selector.get_active_id()), None)
        address = gateway.get("address") if gateway else None
        def query():
            value = self.audio.snapshot(address)
            value["defaults"] = self.audio.default_devices(value)
            return value
        def loaded(value):
            self.audio_snapshot = value
            if value.get("active"):
                text = "Разговор идёт через микрофон и звук компьютера."
            elif value.get("available"):
                text = "Звук готов. Нажми «Использовать звук компьютера» или включи автоматическое подключение."
            else:
                text = "После начала разговора приложение подключит выбранные устройства звука." if gateway else "Сначала подключи телефон для звонков."
            self.audio_status.set_text(text)
            for selector, key, saved, default_key in [(self.microphone_selector, "local_sources", "microphone", "source"),
                                                     (self.speaker_selector, "local_sinks", "speaker", "sink")]:
                old = selector.get_active_id() or self.config.get(saved)
                selector.remove_all()
                for item in value.get(key, []):
                    selector.append(item["name"], item.get("description") or "Устройство звука")
                if old:
                    selector.set_active_id(old)
                if selector.get_active() < 0 and value["defaults"].get(default_key):
                    selector.set_active_id(value["defaults"][default_key])
            self._maybe_auto_audio()
        self.work(query, loaded, busy=False)

    def _smart_start_audio(self):
        gateway = self._gateway()
        if not gateway:
            self._connect_calls()
            return
        if not any(c.get("address") == gateway.get("address") and c.get("state") == "active" for c in self.calls_state.get("calls", [])):
            self.say("Начни или прими звонок — тогда можно подключить звук компьютера.")
            return
        if self.microphone_selector.get_active_id() and self.speaker_selector.get_active_id():
            self._start_audio()
        else:
            self.audio_expander.set_expanded(True)
            self._refresh_audio()
            self.say("Выбери микрофон и динамики ниже. Они сохранятся для следующих звонков.")

    def _maybe_auto_audio(self):
        if self.auto_route_pending or not self.config.get("auto_audio") or self.audio.active:
            return
        address = self.config.get("bluetooth_devices").get(self.current_device)
        if not address:
            return
        active = next((c for c in self.calls_state.get("calls", []) if c.get("address") == address and c.get("state") == "active"), None)
        if not active:
            self.auto_audio_attempt = None
            return
        key = (self.current_device, active.get("path"))
        if key == self.auto_audio_attempt:
            return
        self.auto_route_pending = True
        source = self.microphone_selector.get_active_id() or self.config.get("microphone")
        sink = self.speaker_selector.get_active_id() or self.config.get("speaker")
        def connect():
            value = self.audio.snapshot(address)
            if not value.get("available"):
                return {"pending": True}
            defaults = self.audio.default_devices(value)
            sources = {d["name"] for d in value.get("local_sources", [])}
            sinks = {d["name"] for d in value.get("local_sinks", [])}
            selected_source = source if source in sources else defaults["source"]
            selected_sink = sink if sink in sinks else defaults["sink"]
            if not selected_source or not selected_sink:
                return {"choose": True}
            # A phone switch during the query cannot start audio for the old selection.
            if self.current_device != key[0] or self.config.get("bluetooth_devices").get(self.current_device) != address:
                return {"pending": True}
            return self.audio.start(address, selected_source, selected_sink)
        def done(value):
            self.auto_route_pending = False
            if value.get("pending"):
                return
            self.auto_audio_attempt = key
            if value.get("choose"):
                self.audio_status.set_text("Выбери микрофон и динамики для первого разговора.")
                self.audio_expander.set_expanded(True)
            else:
                self.audio_status.set_text("Разговор идёт через микрофон и звук компьютера.")
        def failed(text):
            self.auto_route_pending = False
            self.auto_audio_attempt = key
            self.audio_status.set_text("Звук не подключился автоматически. Нажми «Использовать звук компьютера».")
        self.work(connect, done, busy=False, on_error=failed)

    def _prepare_audio(self):
        gateway = self._gateway()
        if gateway:
            self.work(lambda: self.audio.prepare(gateway.get("address", "")), lambda _: self._refresh_audio(), "Переключаем телефон в профиль звонков…")

    def _start_audio(self):
        gateway = self._gateway()
        if not gateway:
            return
        microphone = self.microphone_selector.get_active_id()
        speaker = self.speaker_selector.get_active_id()
        if not microphone or not speaker:
            self.say("Выбери микрофон и динамики ноутбука.", True)
            return
        address = gateway.get("address", "")
        self._save(bluetooth_address=address, microphone=microphone, speaker=speaker)
        self.work(lambda: self.audio.start(address, microphone, speaker),
                  lambda _: (self.say("Двусторонний маршрут звука создан."), self._refresh_audio()), "Подключаем звук разговора…")

    def _stop_audio(self):
        def stopped(value):
            self.say(value.get("status", "Отключение завершено."),
                     error=bool(value.get("errors") or value.get("active")))
            self._refresh_audio()
        self.work(self.audio.stop, stopped)

    def _pause_call_audio(self):
        address = self.config.get("bluetooth_devices").get(self.current_device)
        active = next((c for c in self.calls_state.get("calls", [])
                       if c.get("address") == address and c.get("state") == "active"), None)
        if active:
            self.auto_audio_attempt = (self.current_device, active.get("path"))
        self._stop_audio()

    def _choose_files(self):
        if not self._device():
            return
        dialog = Gtk.FileChooserDialog(title="Отправить файлы", transient_for=self.window, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("Отмена", Gtk.ResponseType.CANCEL, "Отправить", Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)
        if dialog.run() == Gtk.ResponseType.OK:
            paths = dialog.get_filenames()
            dialog.destroy()
            self._share_paths(paths)
        else:
            dialog.destroy()

    def _share_paths(self, paths):
        device = self._device()
        if not device:
            return
        files = [str(Path(p).expanduser().resolve()) for p in paths if Path(p).expanduser().is_file()]
        if len(files) != len(paths):
            self.say("Передавать можно обычные файлы. Папку сначала упакуй в ZIP.", True)
            return
        def send():
            for path in files:
                self.kde.share_file(device, path)
            return len(files)
        self.work(send, lambda count: self.say(f"Передача запрошена: {count} файл(ов). Прогресс — в системных уведомлениях."), "Начинаем передачу…")

    def _files_dropped(self, widget, context, x, y, data, info, time):
        paths = []
        for uri in data.get_uris() or []:
            item = Gio.File.new_for_uri(uri)
            path = item.get_path()
            if path:
                paths.append(path)
        if paths:
            self._share_paths(paths)
        Gtk.drag_finish(context, bool(paths), False, time)

    def _send_url(self):
        url = self.url_entry.get_text().strip()
        if not url.startswith(("https://", "http://")):
            self.say("Укажи ссылку, начинающуюся с https:// или http://.", True)
            return
        self.device_action(lambda d: self.kde.share_url(d, url), "Ссылка передана телефону.")

    def _open_downloads(self):
        path = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD) or str(Path.home() / "Downloads")
        Path(path).mkdir(parents=True, exist_ok=True)
        Gio.AppInfo.launch_default_for_uri(Path(path).as_uri(), None)

    def _refresh_adb(self):
        if not hasattr(self, "adb_selector"):
            return
        def loaded(devices):
            previous = self.adb_selector.get_active_id() or self.config.get("adb_serial")
            self.adb_selector.remove_all()
            for device in devices:
                self.adb_selector.append(device["serial"], f"{device.get('model') or device['serial']} · {device['state']}")
            if previous:
                self.adb_selector.set_active_id(previous)
            if self.adb_selector.get_active() < 0 and len(devices) == 1:
                self.adb_selector.set_active(0)
            self.screen_status.set_text("Выбери авторизованное устройство и открой экран." if devices else
                "Телефон для трансляции ещё не подключён. Выполни сопряжение ниже или подключи USB и разреши отладку.")
        self.work(self.screen.devices, loaded, busy=False,
                  on_error=lambda text: self.screen_status.set_text(text))

    def _pair_adb(self):
        endpoint = self.pair_endpoint.get_text().strip()
        code = self.pair_code.get_text().strip()
        self.pair_code.set_text("")
        if not endpoint or not code:
            self.say("Введи адрес сопряжения и код с телефона.", True)
            return
        self.work(lambda: self.screen.pair(endpoint, code), lambda _: self.say("Сопряжение выполнено. Теперь укажи адрес подключения с основного экрана беспроводной отладки."))

    def _connect_adb(self):
        endpoint = self.connect_endpoint.get_text().strip()
        self.work(lambda: self.screen.connect(endpoint), lambda _: self._refresh_adb(), "Подключаем экран…")

    def _launch_screen(self):
        serial = self.adb_selector.get_active_id()
        if not serial:
            self.say("Сначала подключи и выбери телефон для трансляции.", True)
            return
        self._save(adb_serial=serial)
        screen_off = self.screen_off.get_active()
        def exited(code, message):
            GLib.idle_add(self.say, message or "Трансляция завершена.", code != 0)
        self.work(lambda: self.screen.launch(serial, screen_off=screen_off, max_size=self.config.get("max_size"), on_exit=exited),
                  lambda _: self.say("Трансляция запускается в отдельном окне."))

    def _connect_calls(self):
        if self.bluetooth_pending:
            return
        device = self._device()
        if not device:
            self.select_page("overview")
            return
        self.bluetooth_pending = True
        self.connect_calls_button.set_sensitive(False)
        self.call_setup_hint.set_text("Ищем твой телефон в Bluetooth…")
        name = next((d.get("name", "") for d in self.devices if d["id"] == device), "")
        remembered = self.config.get("bluetooth_devices").get(device)
        def loaded(state):
            self.bluetooth_pending = False
            self.connect_calls_button.set_sensitive(True)
            if self.current_device != device:
                return
            self.bluetooth_state = state
            suggestion = self.bluetooth.suggest_phone(state, name, remembered)
            candidate = suggestion.get("device")
            if candidate and not suggestion.get("needs_confirmation") and state.get("powered"):
                self._connect_bt_device(candidate, device)
            else:
                self._choose_bluetooth(state, device)
        def failed(text):
            self.bluetooth_pending = False
            self.connect_calls_button.set_sensitive(True)
            self.call_setup_hint.set_text(text)
        self.work(self.bluetooth.snapshot, loaded, busy=False, on_error=failed)

    def _enable_calls_setup(self):
        active = [c for c in self.calls_state.get("calls", []) if c.get("state") != "disconnected"]
        if active:
            self.say("Заверши текущий звонок перед настройкой звука.", True)
            return
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True,
            message_type=Gtk.MessageType.QUESTION, buttons=Gtk.ButtonsType.OK_CANCEL,
            text="Настроить звонки на компьютере?")
        dialog.format_secondary_text("Сохраним прежние настройки и перезапустим службу звука. Звук на несколько секунд прервётся. После этого переподключим телефон.")
        approved = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        if not approved:
            return
        candidates = [Path(__file__).resolve().parents[1] / "scripts/ENABLE-CALLS.sh",
                      Path(__file__).resolve().parents[2] / "scripts/ENABLE-CALLS.sh"]
        script = next((p for p in candidates if p.is_file()), None)
        if script is None:
            self.say("Не найден компонент настройки. Обнови VELIQEN Link из полного комплекта.", True)
            return
        self.call_system_button.set_sensitive(False)
        def apply():
            if any(c.get("state") != "disconnected" for c in self.telephony.refresh().get("calls", [])):
                raise RuntimeError("Начался звонок. Заверши его перед настройкой звука.")
            result = subprocess.run(["bash", str(script), "--apply", "--restart"], capture_output=True, text=True, timeout=35)
            if result.returncode:
                raise RuntimeError((result.stderr or result.stdout or "Не удалось настроить звук.").strip()[-700:])
            return True
        def ready(_):
            self.call_system_button.set_sensitive(True)
            self.say("Настройки сохранены. Переподключаем телефон для звонков…")
            GLib.timeout_add_seconds(3, lambda: (self._connect_calls(), False)[1])
        def failed(text):
            self.call_system_button.set_sensitive(True)
            self.call_setup_hint.set_text(text)
        self.work(apply, ready, "Настраиваем звук компьютера…", on_error=failed)

    def _connect_bt_device(self, phone, device_id):
        if self.bluetooth_pending:
            return
        self.bluetooth_pending = True
        self.connect_calls_button.set_sensitive(False)
        self.call_setup_hint.set_text("Подтверди одинаковый код на телефоне и компьютере, если появится запрос.")
        def done(result):
            self.bluetooth_pending = False
            self.connect_calls_button.set_sensitive(True)
            linked = result.get("device") or {}
            if self.current_device == device_id:
                self.call_setup_hint.set_text(result.get("message", "Проверяем подключение…"))
            if linked.get("paired") and result.get("state") in {"connected", "needs_call_setup"}:
                bindings = dict(self.config.get("bluetooth_devices"))
                bindings[device_id] = linked["address"]
                self._save(bluetooth_devices=bindings, bluetooth_address=linked["address"])
            self.say(result.get("message", "Проверяем подключение…"))
            self.refresh()
            GLib.timeout_add_seconds(3, lambda: (self.refresh(), False)[1])
        def failed(text):
            self.bluetooth_pending = False
            self.connect_calls_button.set_sensitive(True)
            self.call_setup_hint.set_text(text)
        self.work(lambda: self.bluetooth.connect_phone(phone["path"]), done,
                  "Подключаем звонки. Подтверди запрос на обоих устройствах.", on_error=failed)

    def _choose_bluetooth(self, initial, device_id):
        dialog = Gtk.Dialog(title="Подключить звонки", transient_for=self.window, modal=True)
        dialog.set_default_size(560, 450)
        dialog.add_button("Закрыть", Gtk.ResponseType.CLOSE)
        content = dialog.get_content_area()
        content.set_border_width(24)
        content.set_spacing(14)
        content.pack_start(label("Выбери телефон для звонков", "heading"), False, False, 0)
        hint = label("На телефоне открой настройки Bluetooth и оставь их открытыми. При подключении сравни код на обоих устройствах.", "muted", True)
        content.pack_start(hint, False, False, 0)
        rows = box()
        content.pack_start(rows, True, True, 0)
        state = {"closed": False, "loading": False, "scanning": False}
        def choose(phone):
            dialog.response(Gtk.ResponseType.CLOSE)
            self._connect_bt_device(phone, device_id)
        def scan():
            if not state["closed"] and not state["scanning"]:
                state["scanning"] = True
                self.work(self.bluetooth.start_discovery,
                          lambda _: self.work(self.bluetooth.stop_discovery, busy=False) if state["closed"] else None,
                          busy=False)
        def power():
            self.work(self.bluetooth.power_on, lambda _: (scan(), poll()), "Включаем Bluetooth…")
        def draw(snapshot):
            state["loading"] = False
            if state["closed"]:
                return
            clear(rows)
            if not snapshot.get("available"):
                rows.pack_start(label("Компьютер пока не видит Bluetooth-адаптер. Открой системные настройки Bluetooth.", "muted", True), False, False, 0)
            elif not snapshot.get("powered"):
                rows.pack_start(button("Включить Bluetooth", power, True), False, False, 0)
            else:
                scan()
                phones = snapshot.get("phones", [])
                if not phones:
                    rows.pack_start(label("Ищем телефон… Оставь его настройки Bluetooth открытыми.", "muted", True), False, False, 0)
                for phone in phones:
                    row = box(False, 12, "card")
                    texts = box(spacing=4)
                    texts.pack_start(label(phone.get("name", "Телефон"), "section", True), False, False, 0)
                    texts.pack_start(label("Ранее сопряжён" if phone.get("paired") else "Потребуется подтверждение кода", "muted"), False, False, 0)
                    row.pack_start(texts, True, True, 0)
                    row.pack_end(button("Подключить", lambda p=phone: choose(p), True), False, False, 0)
                    rows.pack_start(row, False, False, 0)
            rows.show_all()
        def failed(text):
            state["loading"] = False
            if not state["closed"]:
                hint.set_text(text)
        def poll():
            if state["closed"]:
                return False
            if not state["loading"]:
                state["loading"] = True
                self.work(self.bluetooth.snapshot, draw, busy=False, on_error=failed)
            return True
        content.pack_start(style(button("Открыть системные настройки Bluetooth", self._bluetooth_settings), "quiet"), False, False, 0)
        dialog.show_all()
        draw(initial)
        timer = GLib.timeout_add_seconds(2, poll)
        dialog.run()
        state["closed"] = True
        GLib.source_remove(timer)
        dialog.destroy()
        self.work(self.bluetooth.stop_discovery, busy=False)

    def _request_native_screen(self):
        if self.native_session and self.native_session.get("device_id") != self.current_device:
            self._stop_native_screen()
        if self.native_session and self.native_viewer:
            self.native_viewer.show_all()
            self.native_viewer.present()
            return
        if self.native_request_pending:
            self.select_page("screen")
            return
        device = self._device()
        if not device:
            self.select_page("overview")
            self._pair_dialog()
            return
        self.select_page("screen")
        self.native_request_pending = True
        self.native_request_device = device
        self.native_request_sequence += 1
        sequence = self.native_request_sequence
        self.native_screen_button.set_sensitive(False)
        self.native_screen_status.set_text("Отправляем запрос. Разблокируй телефон и нажми уведомление VELIQEN Link.")
        def request():
            if self.current_device != device or sequence != self.native_request_sequence:
                return
            status = self.receiver.start()
            if not status.get("listening"):
                raise RuntimeError(status.get("status") or "Не удалось подготовить приём экрана.")
            if self.current_device == device and sequence == self.native_request_sequence:
                self.kde.ping(device, SCREEN_REQUEST)
        def sent(_):
            if self.current_device != device or sequence != self.native_request_sequence:
                return
            self.native_screen_status.set_text("Запрос отправлен. На телефоне нажми уведомление VELIQEN Link и разреши показ экрана. Можно также нажать «Показать экран» в самом приложении телефона.")
            self.say("Подтверди показ на телефоне — окно откроется автоматически.")
        def failed(text):
            if sequence != self.native_request_sequence:
                return
            self.native_request_pending = False
            self.native_screen_button.set_sensitive(True)
            self.native_screen_status.set_text(text)
        self.work(request, sent, busy=False, on_error=failed)
        def release():
            if sequence != self.native_request_sequence:
                return False
            self.native_request_pending = False
            self.native_screen_button.set_sensitive(True)
            if not self.native_session and self.current_device == device:
                self.native_screen_status.set_text("Ждём подтверждение. Если уведомление не появилось, открой на телефоне VELIQEN Link → «Показать экран». На телефоне нужна версия 1.1 или новее.")
            return False
        GLib.timeout_add_seconds(30, release)

    def _native_status(self, message):
        if not self.closing and hasattr(self, "native_screen_status") and not self.native_request_pending:
            self.native_screen_status.set_text(str(message))
        return False

    def _native_session_started(self, information):
        if self.closing or information.get("device_id") != self.current_device:
            self.receiver.stop_session(session_id=information.get("session_id"))
            return False
        self.native_session = information
        self.native_generation += 1
        self.native_request_pending = False
        self.native_request_sequence += 1
        self.native_screen_button.set_sensitive(True)
        self.native_screen_status.set_text("Экран открыт в отдельном окне. Завершить показ можно здесь или на телефоне.")
        reveal(self.native_stop_button, True)
        with self.native_frame_lock:
            self.native_frame = None
        if self.native_viewer is None:
            try:
                self.native_viewer = ScreenViewer(self, self._send_screen_command, self._stop_native_screen)
            except (ImportError, ValueError):
                self.receiver.stop_session(session_id=information.get("session_id"))
                self._native_session_ended("Не установлен компонент показа экрана. Обнови приложение через INSTALL-UBUNTU.sh.")
                return False
        self.native_viewer.session(information)
        self.native_viewer.show_all()
        self.native_viewer.present()
        self.say("Телефон передаёт экран. Закрой окно, чтобы завершить показ.")
        return False

    def _native_frame_received(self, data, session_id):
        if self.closing:
            return
        with self.native_frame_lock:
            self.native_frame = (session_id, data)
            if self.native_frame_scheduled:
                return
            self.native_frame_scheduled = True
        GLib.idle_add(self._draw_native_frame)

    def _draw_native_frame(self):
        with self.native_frame_lock:
            data, self.native_frame = self.native_frame, None
            self.native_frame_scheduled = False
        if not self.closing and self.native_session and self.native_viewer and data:
            if data[0] == self.native_session.get("session_id"):
                self.native_viewer.frame(data[1])
        return False

    def _send_screen_command(self, command):
        if self.closing or not self.native_session or self.native_session.get("device_id") != self.current_device:
            return
        generation = self.native_generation
        session_id = self.native_session.get("session_id")
        def send():
            if self.closing or generation != self.native_generation or not self.native_session:
                return
            try:
                self.receiver.send_command(command, session_id=session_id)
            except Exception:
                GLib.idle_add(self.say, "Управление прервалось. Заверши показ и подключи экран снова.", True)
        self.screen_input_pool.submit(send)

    def _stop_native_screen(self):
        self.receiver.stop_session()
        self._native_session_ended("Показ экрана завершён.")

    def _native_session_ended(self, message, session_id=None):
        if session_id is not None and (not self.native_session or self.native_session.get("session_id") != session_id):
            return False
        self.native_generation += 1
        self.native_session = None
        self.native_request_pending = False
        self.native_request_sequence += 1
        with self.native_frame_lock:
            self.native_frame = None
        if not self.closing and hasattr(self, "native_screen_status"):
            self.native_screen_status.set_text(message)
            self.native_screen_button.set_sensitive(True)
            reveal(self.native_stop_button, False)
        if self.native_viewer:
            self.native_viewer.ended(message)
        return False

    def _allow_screen_network(self):
        device = self._device()
        if not device:
            return
        self.network_screen_button.set_sensitive(False)
        def allow():
            address = screen_address(self.kde.device(device))
            if self.current_device != device:
                raise RuntimeError("Выбран другой телефон. Повтори действие для него.")
            return open_screen_port(address)
        def done(result):
            self.network_screen_button.set_sensitive(True)
            self.native_screen_status.set_text(result["message"])
            self.say(result["message"], result.get("state") not in {"allowed", "inactive"})
        def failed(text):
            self.network_screen_button.set_sensitive(True)
            self.native_screen_status.set_text(text)
        self.work(allow, done, "Ubuntu может запросить пароль для разрешения подключения.", on_error=failed)

    def _unpair(self):
        if not self.current_device:
            return
        dialog = Gtk.MessageDialog(transient_for=self.window, modal=True, message_type=Gtk.MessageType.QUESTION,
                                   buttons=Gtk.ButtonsType.OK_CANCEL, text="Удалить сопряжение с выбранным телефоном?")
        dialog.format_secondary_text("Для следующего подключения потребуется подтверждение на телефоне.")
        accepted = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        if accepted:
            device = self.current_device
            self.work(lambda: self.kde.unpair(device), lambda _: self.refresh())

    def _show_doctor(self):
        from .doctor import report
        def show(text):
            dialog = Gtk.Dialog(title="Диагностика VELIQEN Link", transient_for=self.window, modal=True)
            dialog.set_default_size(650, 490)
            dialog.add_button("Закрыть", Gtk.ResponseType.CLOSE)
            view = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                left_margin=18, right_margin=18, top_margin=18)
            view.get_buffer().set_text(text)
            dialog.get_content_area().pack_start(scrolled(view), True, True, 0)
            dialog.show_all()
            dialog.run()
            dialog.destroy()
        self.work(report, show)

    def _about(self):
        dialog = Gtk.AboutDialog(transient_for=self.window, modal=True, program_name="VELIQEN Link",
            version=__version__, comments="Связь Ubuntu и VELIQEN OS.\nОткрытая основа: KDE Connect, scrcpy, PipeWire и BlueZ.",
            website="https://veliqen.tech", license_type=Gtk.License.GPL_3_0,
            authors=["VELIQEN", "Авторы KDE Connect, scrcpy, PipeWire и BlueZ — соответствующие компоненты"])
        dialog.run()
        dialog.destroy()

    def _create_indicator(self):
        self.indicator = None
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3
            self.indicator = AyatanaAppIndicator3.Indicator.new("veliqen-link", "veliqen-link",
                AyatanaAppIndicator3.IndicatorCategory.APPLICATION_STATUS)
            self.indicator.set_status(AyatanaAppIndicator3.IndicatorStatus.ACTIVE)
            menu = Gtk.Menu()
            for text, callback in [("Открыть VELIQEN Link", self._present),
                                   ("Позвонить…", lambda: self._open_page("calls")),
                                   ("Сообщения", lambda: self._open_page("messages")),
                                   ("Передать файлы…", lambda: (self._present(), self._choose_files())),
                                   ("Экран телефона", lambda: (self._present(), self._request_native_screen())),
                                   ("Завершить", self.exit_application)]:
                item = Gtk.MenuItem(label=text)
                item.connect("activate", lambda w, fn=callback: fn())
                menu.append(item)
            menu.show_all()
            self.indicator.set_menu(menu)
        except (ImportError, ValueError):
            pass

    def _close_window(self, *_):
        if self.config.get("close_to_background"):
            self.window.hide()
            notification = Gio.Notification.new("VELIQEN Link работает в фоне")
            notification.set_body("Чтобы открыть окно, запусти VELIQEN Link из меню приложений.")
            notification.set_default_action("app.show")
            self.send_notification("background", notification)
            return True
        self.exit_application()
        return True

    def exit_application(self):
        if not self.closing:
            self.closing = True
            if self.clipboard_bridge:
                self.clipboard_bridge.close()
            self.receiver.close()
            self.bluetooth.close()
            self.screen.close()
            self.telephony.close()
            try:
                self.audio.stop()
            except Exception:
                pass
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.screen_input_pool.shutdown(wait=False, cancel_futures=True)
            self.quit()
        return False
