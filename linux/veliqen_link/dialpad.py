"""Compact phone keypad; call control stays in the application/backend."""
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, Gtk


def call_key(call):
    """Identity captured when a user presses a tone key, never a gateway alone."""
    if not call:
        return None
    return tuple(call.get(name, "") for name in ("path", "gateway_path", "address", "number"))


def active_call(calls, gateway):
    """Only an unambiguous active call of this particular paired phone."""
    if not gateway or not gateway.get("address"):
        return None
    matches = [call for call in calls
               if call.get("state") == "active"
               and call.get("address") == gateway["address"]
               and call.get("gateway_path") == gateway["path"]]
    return matches[0] if len(matches) == 1 else None


def add_class(widget, name):
    widget.get_style_context().add_class(name)
    return widget


class Dialpad(Gtk.Box):
    """Visible digits and normal keyboard input, with a separate in-call mode.

    on_call() is invoked only in dial mode. on_tone(digit, call_key) receives
    the identity currently displayed; returning False rejects the key press.
    Draft numbers survive entering/leaving a call. Enter never places a second
    call while the keypad displays the digits of a current conversation.
    """

    def __init__(self, on_call, on_tone):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        add_class(self, "dialpad")
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.on_call = on_call
        self.on_tone = on_tone
        self.mode = "dial"
        self.call_key = None
        self.ready = False
        self.pending = False
        self.draft = ""
        self._updating = False

        self.mode_label = add_class(Gtk.Label(label="Номер телефона"), "muted")
        self.pack_start(self.mode_label, False, False, 0)
        self.entry = add_class(Gtk.Entry(), "dial-number")
        self.entry.set_alignment(0.5)
        self.entry.set_width_chars(18)
        self.entry.set_max_width_chars(20)
        self.entry.set_max_length(80)
        self.entry.set_input_purpose(Gtk.InputPurpose.PHONE)
        self.entry.set_placeholder_text("Введи номер")
        self.entry.get_accessible().set_name("Номер телефона")
        self.entry.connect("activate", self._activate)
        self.entry.connect("changed", self._changed)
        self.entry.connect("key-press-event", self._key_pressed)
        self.pack_start(self.entry, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=10)
        grid.set_halign(Gtk.Align.CENTER)
        self.keys = {}
        letters = ("", "ABC", "DEF", "GHI", "JKL", "MNO", "PQRS", "TUV", "WXYZ", "", "+", "")
        for index, (digit, subtitle) in enumerate(zip("123456789*0#", letters)):
            key = add_class(Gtk.Button(), "dial-key")
            key.set_size_request(78, 68)
            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            content.pack_start(add_class(Gtk.Label(label=digit), "dial-digit"), True, True, 0)
            content.pack_start(add_class(Gtk.Label(label=subtitle or " "), "dial-letters"), False, False, 0)
            key.add(content)
            key.get_accessible().set_name(digit + (" " + subtitle if subtitle else ""))
            key.connect("clicked", lambda _button, value=digit: self.press(value))
            key.connect("key-press-event", self._key_pressed)
            grid.attach(key, index % 3, index // 3, 1, 1)
            self.keys[digit] = key
        self.pack_start(grid, False, False, 0)

        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        controls.set_halign(Gtk.Align.CENTER)
        self.plus_button = add_class(Gtk.Button(label="+"), "dial-tool")
        self.plus_button.set_size_request(46, 48)
        self.plus_button.set_tooltip_text("Международный код номера")
        self.plus_button.connect("clicked", lambda *_: self.press("+"))
        self.plus_button.connect("key-press-event", self._key_pressed)
        controls.pack_start(self.plus_button, False, False, 0)
        self.call_button = add_class(Gtk.Button(label="Позвонить"), "dial-call")
        self.call_button.set_size_request(154, 48)
        self.call_button.connect("clicked", self._activate)
        self.call_button.connect("key-press-event", self._key_pressed)
        controls.pack_start(self.call_button, False, False, 0)
        self.erase_button = add_class(Gtk.Button(), "dial-tool")
        self.erase_button.add(Gtk.Image.new_from_icon_name("edit-clear-symbolic", Gtk.IconSize.BUTTON))
        self.erase_button.set_size_request(46, 48)
        self.erase_button.get_accessible().set_name("Стереть последнюю цифру")
        self.erase_button.set_tooltip_text("Стереть последнюю цифру")
        self.erase_button.connect("clicked", lambda *_: self.erase())
        self.erase_button.connect("key-press-event", self._key_pressed)
        controls.pack_start(self.erase_button, False, False, 0)
        self.pack_start(controls, False, False, 0)
        self._update_primary()

    def set_number(self, number):
        if self.mode != "dial":
            return False
        self.entry.set_text(str(number))
        self.entry.set_position(-1)
        self.entry.grab_focus()
        return True

    def set_state(self, mode="dial", ready=False, pending=False, call_key=None):
        if mode not in {"dial", "tones", "blocked"}:
            raise ValueError("Unknown keypad mode")
        old_mode, old_key = self.mode, self.call_key
        if old_mode == "dial":
            self.draft = self.entry.get_text()
        self.mode, self.call_key = mode, call_key
        self.ready, self.pending = bool(ready), bool(pending)
        self._updating = True
        if mode == "dial" and old_mode != "dial":
            self.entry.set_text(self.draft)
        elif mode == "tones" and (old_mode != "tones" or old_key != call_key):
            self.entry.set_text("")
        elif mode == "blocked" and old_mode == "tones":
            self.entry.set_text("")
        self._updating = False
        self.entry.set_editable(mode == "dial")
        self.entry.set_placeholder_text("Цифры для голосового меню" if mode == "tones" else "Введи номер")
        self.entry.get_accessible().set_name("Клавиатура вызова" if mode == "tones" else "Номер телефона")
        self.mode_label.set_text("Клавиатура вызова" if mode == "tones" else
                                 "Текущий вызов" if mode == "blocked" else "Номер телефона")
        for key in self.keys.values():
            key.set_sensitive(mode != "blocked")
        self.plus_button.set_sensitive(mode == "dial")
        self.erase_button.set_sensitive(mode != "blocked")
        self.erase_button.set_tooltip_text("Стереть с экрана; отправленные цифры не отменяются" if mode == "tones" else "Стереть последнюю цифру")
        self._update_primary()

    def _changed(self, *_):
        if not self._updating and self.mode == "dial":
            self.draft = self.entry.get_text()
        self._update_primary()

    def _update_primary(self):
        self.call_button.set_label("Разговор идёт" if self.mode == "tones" else
                                   "Вызов идёт" if self.mode == "blocked" else
                                   "Вызываем…" if self.pending else "Позвонить")
        self.call_button.set_sensitive(self.mode == "dial" and self.ready and not self.pending
                                       and bool(self.entry.get_text().strip()))

    def _activate(self, *_):
        if self.mode == "dial" and self.call_button.get_sensitive():
            self.on_call()

    def press(self, digit):
        if digit not in "0123456789*#+" or len(digit) != 1 or self.mode == "blocked":
            return False
        if self.mode == "tones":
            if digit == "+" or not self.call_key or self.on_tone(digit, self.call_key) is False:
                return False
            self.entry.set_text((self.entry.get_text() + digit)[-40:])
            self.entry.set_position(-1)
            return True
        selection = self.entry.get_selection_bounds()
        if selection:
            self.entry.delete_text(*selection)
        position = self.entry.get_position()
        self.entry.set_position(self.entry.insert_text(digit, position))
        return True

    def erase(self):
        if self.mode == "blocked":
            return
        selection = self.entry.get_selection_bounds()
        if selection:
            self.entry.delete_text(*selection)
        else:
            position = self.entry.get_position()
            if position > 0:
                self.entry.delete_text(position - 1, position)

    def _key_pressed(self, widget, event):
        if event.state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._activate()
            return True
        if event.keyval == Gdk.KEY_BackSpace:
            if widget is self.entry and self.mode == "dial":
                return False
            self.erase()
            return True
        codepoint = Gdk.keyval_to_unicode(event.keyval)
        digit = chr(codepoint) if codepoint else ""
        if digit and digit in "0123456789*#+":
            if widget is self.entry and self.mode == "dial":
                return False
            self.press(digit)
            return True
        return self.mode != "dial" and widget is self.entry
