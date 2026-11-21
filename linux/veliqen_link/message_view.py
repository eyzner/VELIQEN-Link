"""Native message bubbles; display text only, never markup or remote content."""
from datetime import datetime
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango


class MessageView(Gtk.ScrolledWindow):
    def __init__(self):
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.set_min_content_height(180)
        self.rows = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.rows.set_border_width(16)
        self.add(self.rows)
        self._buffer = Gtk.TextBuffer()
        self._buffer.connect("changed", self._placeholder_changed)

    def get_buffer(self):
        # Existing empty-state and draft handlers only set text on this buffer.
        return self._buffer

    def _clear(self):
        for widget in self.rows.get_children():
            self.rows.remove(widget)

    def _placeholder_changed(self, buffer):
        self._clear()
        value = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), True)
        item = Gtk.Label(label=value, xalign=0.5)
        item.set_line_wrap(True)
        item.set_max_width_chars(40)
        item.set_justify(Gtk.Justification.CENTER)
        item.set_margin_top(48)
        item.get_style_context().add_class("muted")
        self.rows.pack_start(item, False, False, 0)
        self.rows.show_all()

    def show_messages(self, messages):
        self._clear()
        def timestamp(item):
            try:
                return float(item.get("date") or 0)
            except (ValueError, TypeError, OverflowError):
                return 0
        for item in sorted(messages, key=timestamp)[-100:]:
            incoming = item.get("incoming", item.get("type") == 1)
            bubble = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            bubble.get_style_context().add_class("message-in" if incoming else "message-out")
            bubble.set_halign(Gtk.Align.START if incoming else Gtk.Align.END)
            body = Gtk.Label(label=str(item.get("body") or ""), xalign=0)
            body.set_selectable(True)
            body.set_line_wrap(True)
            body.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            body.set_max_width_chars(32)
            bubble.pack_start(body, False, False, 0)
            try:
                stamp = datetime.fromtimestamp(float(item.get("date", 0)) / 1000).strftime("%d.%m · %H:%M")
            except (ValueError, TypeError, OSError, OverflowError):
                stamp = ""
            note = Gtk.Label(label=stamp, xalign=1)
            note.get_style_context().add_class("small")
            bubble.pack_start(note, False, False, 0)
            self.rows.pack_start(bubble, False, False, 0)
        self.rows.show_all()
        GLib.idle_add(self._scroll_end)

    def _scroll_end(self):
        adjustment = self.get_vadjustment()
        adjustment.set_value(max(adjustment.get_lower(), adjustment.get_upper()-adjustment.get_page_size()))
        return False
