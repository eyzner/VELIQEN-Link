"""Optional GNOME bridge. Content is never persisted or logged."""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk, Gdk

NAME = "tech.veliqen.Link.Clipboard"
PATH = "/tech/veliqen/Link/Clipboard"
IFACE = NAME

class ClipboardBridge:
    def __init__(self, callback):
        self.callback = callback
        self.enabled = False
        self.last = None
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.subscription = self.bus.signal_subscribe(NAME, IFACE, "Changed", PATH, None,
            Gio.DBusSignalFlags.NONE, self._changed)
        self.clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        display = Gdk.Display.get_default()
        self.x11 = display is not None and "X11" in type(display).__name__
        self.owner_handler = self.clipboard.connect("owner-change", self._owner) if self.x11 else None
        self.watch = Gio.bus_watch_name_on_connection(self.bus, NAME, Gio.BusNameWatcherFlags.NONE,
            lambda *_: self._configure(), None)

    def _changed(self, connection, sender, path, interface, name, parameters):
        text, = parameters.unpack()
        self._forward(text)

    def _owner(self, *_):
        if self.enabled:
            self.clipboard.request_text(lambda clipboard, text, data: self._forward(text), None)

    def _forward(self, text):
        if not self.enabled or not isinstance(text, str) or len(text.encode("utf-8")) > 65536:
            return
        if not text or text == self.last:
            return
        self.last = text
        self.callback(text)

    def available(self):
        if self.x11:
            return True
        try:
            result = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                "org.freedesktop.DBus", "NameHasOwner", GLib.Variant("(s)", (NAME,)),
                GLib.VariantType.new("(b)"), Gio.DBusCallFlags.NONE, 1500, None)
            return result.unpack()[0]
        except GLib.Error:
            return False

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        self.last = None
        self._configure()

    def _configure(self):
        try:
            self.bus.call_sync(NAME, PATH, IFACE, "SetEnabled",
                GLib.Variant("(b)", (self.enabled and not self.x11,)), None,
                Gio.DBusCallFlags.NONE, 1500, None)
        except GLib.Error:
            pass

    def close(self):
        self.enabled = False
        self._configure()
        Gio.bus_unwatch_name(self.watch)
        self.bus.signal_unsubscribe(self.subscription)
        if self.owner_handler is not None:
            self.clipboard.disconnect(self.owner_handler)
