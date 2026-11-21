"""Small native viewer for the explicitly approved phone screen session."""
import math
import time

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, Gdk, GdkPixbuf


class ScreenViewer(Gtk.ApplicationWindow):
    def __init__(self, application, send, stop):
        gi.require_foreign("cairo")
        super().__init__(application=application, title="Экран телефона — VELIQEN Link")
        self.set_default_size(480, 820)
        self.set_size_request(350, 430)
        self._send = send
        self._stop = stop
        self._pixbuf = None
        self._control = False
        self._press = None
        self._last_scroll = 0.0
        self._rect = (0, 0, 0, 0)
        self.connect("delete-event", self._closed)

        header = Gtk.HeaderBar(title="Экран телефона", show_close_button=True)
        self.header = header
        stop_button = Gtk.Button(label="Завершить")
        stop_button.connect("clicked", lambda *_: self._closed())
        header.pack_end(stop_button)
        self.set_titlebar(header)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(root)
        self.status = Gtk.Label(label="Ждём изображение с телефона…", xalign=0)
        self.status.set_line_wrap(True)
        self.status.set_margin_start(14)
        self.status.set_margin_end(14)
        self.status.set_margin_top(10)
        self.status.set_margin_bottom(10)
        root.pack_start(self.status, False, False, 0)
        self.canvas = Gtk.DrawingArea()
        self.canvas.set_hexpand(True)
        self.canvas.set_vexpand(True)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK |
                               Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.canvas.connect("draw", self._draw)
        self.canvas.connect("button-press-event", self._pressed)
        self.canvas.connect("button-release-event", self._released)
        self.canvas.connect("scroll-event", self._scroll)
        root.pack_start(self.canvas, True, True, 0)

        self.controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.controls.set_border_width(12)
        row = Gtk.Box(spacing=8, homogeneous=True)
        for title, op in [("Назад", "back"), ("Домой", "home"), ("Приложения", "recents")]:
            item = Gtk.Button(label=title)
            item.connect("clicked", lambda _w, value=op: self._command({"op": value}))
            row.pack_start(item, True, True, 0)
        self.controls.pack_start(row, False, False, 0)
        row = Gtk.Box(spacing=8)
        self.text = Gtk.Entry()
        self.text.set_placeholder_text("Ввести текст на телефоне")
        self.text.set_max_length(1024)
        self.text.connect("activate", self._submit_text)
        row.pack_start(self.text, True, True, 0)
        send_button = Gtk.Button(label="Ввести")
        send_button.connect("clicked", self._submit_text)
        row.pack_end(send_button, False, False, 0)
        self.controls.pack_start(row, False, False, 0)
        self.controls.set_sensitive(False)
        root.pack_end(self.controls, False, False, 0)

    def session(self, information):
        self._pixbuf = None
        self._press = None
        self._control = bool(information.get("control"))
        self.controls.set_sensitive(self._control)
        self.header.set_title(information.get("name") or "Экран телефона")
        self.status.set_text("Нажимай мышью. Прокручивай колёсиком или зажми и проведи." if self._control else
            "Только просмотр. Для управления заверши показ и включи управление перед новым запуском на телефоне.")
        self.canvas.queue_draw()

    def frame(self, data):
        # Receiver has a transport size bound; also bound image dimensions before allocating.
        loader = GdkPixbuf.PixbufLoader.new_with_type("jpeg")
        invalid = []
        def dimensions(_loader, width, height):
            if width < 1 or height < 1 or width > 4096 or height > 4096 or width * height > 12_000_000:
                invalid.append(True)
                _loader.set_size(1, 1)
        loader.connect("size-prepared", dimensions)
        try:
            loader.write(data)
            loader.close()
            if invalid:
                raise ValueError("Размер изображения не поддерживается")
            pixbuf = loader.get_pixbuf()
            if pixbuf is not None:
                if self._pixbuf is not None and (pixbuf.get_width(), pixbuf.get_height()) != (self._pixbuf.get_width(), self._pixbuf.get_height()):
                    self._press = None
                self._pixbuf = pixbuf
                self.canvas.queue_draw()
        except Exception:
            self.status.set_text("Не удалось прочитать изображение. Заверши показ и попробуй снова.")

    def ended(self, message):
        self._control = False
        self._pixbuf = None
        self._press = None
        self.controls.set_sensitive(False)
        self.text.set_text("")
        self.status.set_text(message or "Показ экрана завершён. Можно закрыть это окно.")
        self.canvas.queue_draw()

    def _draw(self, widget, context):
        allocation = widget.get_allocation()
        context.set_source_rgb(0.045, 0.05, 0.06)
        context.paint()
        if self._pixbuf is None:
            self._rect = (0, 0, 0, 0)
            return False
        width, height = self._pixbuf.get_width(), self._pixbuf.get_height()
        scale = min(allocation.width / width, allocation.height / height)
        target_w, target_h = width * scale, height * scale
        x, y = (allocation.width - target_w) / 2, (allocation.height - target_h) / 2
        self._rect = (x, y, target_w, target_h)
        context.save()
        context.translate(x, y)
        context.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(context, self._pixbuf, 0, 0)
        context.paint()
        context.restore()
        return False

    def _point(self, event):
        x, y, width, height = self._rect
        if width <= 0 or height <= 0 or not (x <= event.x <= x + width and y <= event.y <= y + height):
            return None
        return ((event.x - x) / width, (event.y - y) / height)

    def _pressed(self, _widget, event):
        point = self._point(event)
        self._press = (point, time.monotonic()) if event.button == 1 and self._control and point else None
        return bool(self._press)

    def _released(self, _widget, event):
        press, self._press = self._press, None
        point = self._point(event)
        if not press or not point or not self._control or event.button != 1:
            return False
        start, stamp = press
        elapsed = min(1500, max(80, int((time.monotonic() - stamp) * 1000)))
        if math.hypot(point[0] - start[0], point[1] - start[1]) < 0.015:
            self._command({"op": "tap", "x": point[0], "y": point[1]})
        else:
            self._command({"op": "swipe", "x": start[0], "y": start[1],
                           "x2": point[0], "y2": point[1], "duration": elapsed})
        return True

    def _command(self, command):
        if self._control:
            self._send(command)

    def _scroll(self, _widget, event):
        if not self._control or self._point(event) is None:
            return False
        now = time.monotonic()
        if now - self._last_scroll < 0.18:
            return True
        direction = event.direction
        if direction == Gdk.ScrollDirection.SMOOTH:
            ok, dx, dy = event.get_scroll_deltas()
            if not ok or (dx == 0 and dy == 0):
                return True
            direction = Gdk.ScrollDirection.DOWN if dy > 0 else Gdk.ScrollDirection.UP
        if direction in (Gdk.ScrollDirection.UP, Gdk.ScrollDirection.DOWN):
            start, end = (0.72, 0.28) if direction == Gdk.ScrollDirection.DOWN else (0.28, 0.72)
            self._command({"op": "swipe", "x": 0.5, "y": start, "x2": 0.5, "y2": end, "duration": 220})
            self._last_scroll = now
            return True
        return False

    def _submit_text(self, *_):
        text = self.text.get_text()
        if self._control and text:
            self._command({"op": "text", "text": text[:1024]})
            self.text.set_text("")

    def _closed(self, *_):
        self._stop()
        self.ended("Показ экрана завершён.")
        self.hide()
        return True
