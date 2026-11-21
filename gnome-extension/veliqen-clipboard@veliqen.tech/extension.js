// SPDX-License-Identifier: GPL-3.0-or-later
// Text is never persisted. Clipboard monitoring is enabled by the desktop client.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import St from 'gi://St';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const BUS_NAME = 'tech.veliqen.Link.Clipboard';
const OBJECT_PATH = '/tech/veliqen/Link/Clipboard';
const XML = '<node><interface name="tech.veliqen.Link.Clipboard">' +
    '<method name="SetEnabled"><arg name="enabled" type="b" direction="in"/></method>' +
    '<signal name="Changed"><arg name="text" type="s"/></signal>' +
    '</interface></node>';

export default class VeliqenClipboard extends Extension {
    enable() {
        this._alive = true;
        this._monitoring = false;
        this._lastText = null;
        this._idle = 0;
        this._clientWatch = 0;
        this._reading = false;
        this._generation = 0;
        this._selection = global.display.get_selection();
        this._owner = this._selection.connect('owner-changed', (_selection, type) => {
            if (type !== Meta.SelectionType.SELECTION_CLIPBOARD)
                return;
            this._schedule();
        });
        this._exported = Gio.DBusExportedObject.wrapJSObject(XML, {
            SetEnabledAsync: (parameters, invocation) => {
                const [enabled] = parameters;
                this._monitoring = Boolean(enabled);
                this._lastText = null;
                this._generation++;
                if (this._clientWatch)
                    Gio.bus_unwatch_name(this._clientWatch);
                this._clientWatch = 0;
                if (enabled) {
                    this._clientWatch = Gio.bus_watch_name_on_connection(
                        invocation.get_connection(), invocation.get_sender(),
                        Gio.BusNameWatcherFlags.NONE, null, () => {
                            this._monitoring = false;
                            this._lastText = null;
                            this._generation++;
                        });
                }
                invocation.return_value(null);
            },
        });
        this._busOwner = Gio.bus_own_name(Gio.BusType.SESSION, BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            connection => this._exported?.export(connection, OBJECT_PATH), null, null);
    }

    _schedule() {
        if (!this._alive || !this._monitoring || this._idle || this._reading || Main.sessionMode.isLocked)
            return;
        this._idle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._idle = 0;
            if (!this._alive || !this._monitoring || Main.sessionMode.isLocked)
                return GLib.SOURCE_REMOVE;
            const types = this._selection.get_mimetypes(Meta.SelectionType.SELECTION_CLIPBOARD);
            if (types.includes('x-kde-passwordManagerHint') || types.includes('application/x-keepassxc'))
                return GLib.SOURCE_REMOVE;
            const generation = this._generation;
            this._reading = true;
            St.Clipboard.get_default().get_text(St.ClipboardType.CLIPBOARD, (_clipboard, text) => {
                this._reading = false;
                if (!this._alive || !this._monitoring || generation !== this._generation || Main.sessionMode.isLocked)
                    return;
                if (typeof text !== 'string' || !text || text === this._lastText ||
                    new TextEncoder().encode(text).length > 65536)
                    return;
                this._lastText = text;
                this._exported.emit_signal('Changed', new GLib.Variant('(s)', [text]));
            });
            return GLib.SOURCE_REMOVE;
        });
    }

    disable() {
        this._alive = false;
        this._monitoring = false;
        this._generation++;
        this._lastText = null;
        if (this._idle)
            GLib.source_remove(this._idle);
        this._idle = 0;
        if (this._owner)
            this._selection.disconnect(this._owner);
        this._owner = 0;
        if (this._clientWatch)
            Gio.bus_unwatch_name(this._clientWatch);
        this._clientWatch = 0;
        this._exported?.unexport();
        this._exported = null;
        if (this._busOwner)
            Gio.bus_unown_name(this._busOwner);
        this._busOwner = 0;
        this._selection = null;
    }
}
