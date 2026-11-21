"""Wire-level tests against a private D-Bus, never the user's real session.

Run: /usr/bin/python3 -m unittest -v test_kde
Requires the same python3-gi dependency as the application and dbus-daemon.
"""

from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest

from gi.repository import Gio, GLib

from veliqen_link.kde import (
    ApiUnavailable, CONVERSATIONS_INTERFACE, DEVICE_INTERFACE, KdeBackend,
    KdeError, MESSAGE_SIGNATURE, ROOT, SERVICE, decode_message,
)


DEVICE = ROOT + "/devices/testphone"


def connect(address):
    return Gio.DBusConnection.new_for_address_sync(
        address, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
        Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


class PrivateKdeService:
    def __init__(self, modern_sms=True, message_ping=False):
        self.modern_sms = modern_sms
        self.message_ping = message_ping
        self.calls = []
        self.paired = True
        self.reachable = True
        self.peer_request = False
        self.fail_send = False
        self.error = None
        # The managed workspace forbids Unix sockets but permits loopback
        # listeners. Use an authenticated, ephemeral test bus bound ONLY to
        # 127.0.0.1. No anonymous authentication and no external interfaces.
        self.directory = tempfile.TemporaryDirectory(prefix="veliqen-kde-test-")
        configuration = Path(self.directory.name) / "bus.conf"
        configuration.write_text('''<busconfig>
          <type>session</type>
          <listen>tcp:host=127.0.0.1,port=0,family=ipv4</listen>
          <auth>DBUS_COOKIE_SHA1</auth>
          <policy context="default">
            <allow own="org.kde.kdeconnect"/>
            <allow send_destination="*"/>
            <allow receive_sender="*"/>
          </policy>
        </busconfig>''')
        self.daemon = subprocess.Popen(
            ["dbus-daemon", "--config-file=" + str(configuration), "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.address = self.daemon.stdout.readline().strip()
        if not self.address:
            if self.daemon.poll() is None:
                self.daemon.terminate()
            _stdout, error = self.daemon.communicate(timeout=3)
            self.directory.cleanup()
            if "Operation not permitted" in error or "Permission denied" in error:
                raise unittest.SkipTest("This environment forbids private D-Bus sockets: " + error.strip())
            raise RuntimeError("Private dbus-daemon did not start: " + error.strip())
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            self.daemon.terminate()
            self.daemon.communicate(timeout=3)
            self.directory.cleanup()
            raise RuntimeError("Private service registration timed out")
        if self.error:
            self.daemon.terminate()
            self.daemon.communicate(timeout=3)
            self.directory.cleanup()
            raise self.error
        self.client = connect(self.address)

    @staticmethod
    def message(uid=7, thread=42, body="Привет", date=1763659000000):
        return GLib.Variant(MESSAGE_SIGNATURE,
                            (1, body, [("+381600000001",)], date, 1, 0,
                             thread, uid, 2, []))

    def register(self, path, xml):
        node = Gio.DBusNodeInfo.new_for_xml("<node>" + xml + "</node>")
        for interface in node.interfaces:
            self.registrations.append(self.bus.register_object(
                path, interface, self.call, self.get_property, None))

    def run(self):
        self.context = GLib.MainContext.new()
        self.context.push_thread_default()
        self.loop = GLib.MainLoop.new(self.context, False)
        self.registrations = []
        try:
            self.bus = connect(self.address)
            self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                               "org.freedesktop.DBus", "RequestName",
                               GLib.Variant("(su)", (SERVICE, 0)), None,
                               Gio.DBusCallFlags.NONE, 2000, None)
            self.register(ROOT, f'''
              <interface name="{SERVICE}.daemon">
                <method name="devices"><arg type="b" direction="in"/><arg type="b" direction="in"/><arg type="as" direction="out"/></method>
                <method name="forceOnNetworkChange"/>
              </interface>''')
            self.register(DEVICE, f'''
              <interface name="{DEVICE_INTERFACE}">
                <property name="name" type="s" access="read"/>
                <property name="type" type="s" access="read"/>
                <property name="isPaired" type="b" access="read"/>
                <property name="isReachable" type="b" access="read"/>
                <property name="isPairRequestedByPeer" type="b" access="read"/>
                <method name="requestPairing"/>
                <method name="acceptPairing"/>
                <method name="unpair"/>
              </interface>
              <interface name="{CONVERSATIONS_INTERFACE}">
                <method name="activeConversations"><arg type="av" direction="out"/></method>
                <method name="requestAllConversationThreads"/>
                <method name="requestConversation"><arg type="x"/><arg type="i"/><arg type="i"/></method>
                <method name="sendWithoutConversation"><arg type="av"/><arg type="s"/><arg type="av"/></method>
                <signal name="conversationCreated"><arg type="v"/></signal>
                <signal name="conversationUpdated"><arg type="v"/></signal>
              </interface>''')
            self.register(DEVICE + "/share", f'''
              <interface name="{DEVICE_INTERFACE}.share">
                <method name="shareUrl"><arg type="s"/></method>
                <method name="shareText"><arg type="s"/></method>
              </interface>''')
            self.register(DEVICE + "/sms", f'''
              <interface name="{DEVICE_INTERFACE}.sms">
                {('<method name="sendSms"><arg type="av"/><arg type="s"/><arg type="av"/><arg type="x"/></method>' if self.modern_sms else '')}
              </interface>''')
            self.register(DEVICE + "/battery", f'''
              <interface name="{DEVICE_INTERFACE}.battery">
                <property name="charge" type="i" access="read"/>
                <property name="isCharging" type="b" access="read"/>
                <property name="hasBattery" type="b" access="read"/>
              </interface>''')
            for plugin, method in (("ping", "sendPing"), ("findmyphone", "ring"), ("clipboard", "sendClipboard")):
                argument = '<arg type="s" direction="in"/>' if plugin == "ping" and self.message_ping else ''
                self.register(DEVICE + "/" + plugin,
                              f'<interface name="{DEVICE_INTERFACE}.{plugin}"><method name="{method}">{argument}</method></interface>')
            self.ready.set()
            self.loop.run()
            for registration in self.registrations:
                self.bus.unregister_object(registration)
            self.bus.close_sync(None)
        except Exception as error:
            self.error = error
            self.ready.set()
        finally:
            self.context.pop_thread_default()

    def get_property(self, _connection, _sender, _path, _interface, name):
        values = {"name": GLib.Variant("s", "VELIQEN test"), "type": GLib.Variant("s", "phone"),
                  "isPaired": GLib.Variant("b", self.paired),
                  "isReachable": GLib.Variant("b", self.reachable),
                  "isPairRequestedByPeer": GLib.Variant("b", self.peer_request),
                  "charge": GLib.Variant("i", 73), "isCharging": GLib.Variant("b", True),
                  "hasBattery": GLib.Variant("b", True)}
        return values[name]

    def call(self, _connection, _sender, path, interface, method, parameters, invocation):
        self.calls.append((path, interface, method, parameters))
        if method == "devices":
            invocation.return_value(GLib.Variant("(as)", (["testphone"],)))
        elif method == "activeConversations":
            invocation.return_value(GLib.Variant("(av)", ([self.message()],)))
        elif method == "requestConversation":
            # Two messages deliberately have the same timestamp. IDs matter.
            for message in (self.message(7), self.message(8, body="Ещё сообщение"),
                            self.message(99, thread=999, body="Чужой диалог")):
                self.bus.emit_signal(None, DEVICE, CONVERSATIONS_INTERFACE,
                                     "conversationUpdated", GLib.Variant("(v)", (message,)))
            invocation.return_value(None)
        elif self.fail_send and method == "sendSms":
            invocation.return_dbus_error("org.freedesktop.DBus.Error.AccessDenied", "blocked in isolated test")
        else:
            if method == "unpair":
                self.paired = False
            invocation.return_value(None)

    def close(self):
        self.client.close_sync(None)
        self.loop.quit()
        self.context.wakeup()
        self.thread.join(3)
        self.daemon.terminate()
        self.daemon.wait(timeout=3)
        self.daemon.stdout.close()
        self.daemon.stderr.close()
        self.directory.cleanup()


class VariantTransportService:
    """Socket-free test double; wire values still use actual GLib.Variant."""

    message = staticmethod(PrivateKdeService.message)

    def __init__(self, modern_sms=True, message_ping=False):
        self.modern_sms = modern_sms
        self.calls = []
        self.paired = True
        self.reachable = True
        self.peer_request = False
        self.fail_send = False
        self.client = self
        self.subscriptions = {}
        self.next_subscription = 0
        # This table describes the upstream D-Bus API independently from the
        # adapter. Unknown paths and signature mismatches fail in the fixture.
        self.methods = {
            (ROOT, SERVICE + ".daemon"): {"devices": "bb", "forceOnNetworkChange": ""},
            (DEVICE, DEVICE_INTERFACE): {"requestPairing": "", "acceptPairing": "", "unpair": ""},
            (DEVICE, CONVERSATIONS_INTERFACE): {"activeConversations": "", "requestAllConversationThreads": "",
                                                "requestConversation": "xii", "sendWithoutConversation": "avsav"},
            (DEVICE + "/share", DEVICE_INTERFACE + ".share"): {"shareUrl": "s", "shareText": "s"},
            (DEVICE + "/sms", DEVICE_INTERFACE + ".sms"): {"sendSms": "avsavx"} if modern_sms else {},
            (DEVICE + "/ping", DEVICE_INTERFACE + ".ping"): {"sendPing": "s" if message_ping else ""},
            (DEVICE + "/findmyphone", DEVICE_INTERFACE + ".findmyphone"): {"ring": ""},
            (DEVICE + "/clipboard", DEVICE_INTERFACE + ".clipboard"): {"sendClipboard": ""},
        }

    def call_sync(self, _service, path, interface, method, parameters, _return_type, _flags, _timeout, _cancellable):
        if method == "Introspect":
            xml = "<node>"
            for (known_path, known_interface), methods in self.methods.items():
                if path != known_path:
                    continue
                xml += f'<interface name="{known_interface}">'
                for name, signature in methods.items():
                    # GLib parses the full tuple into individual argument types.
                    xml += f'<method name="{name}">'
                    for child_type in GLib.Variant.split_signature("(" + signature + ")"):
                        xml += f'<arg type="{child_type}" direction="in"/>'
                    xml += '</method>'
                xml += '</interface>'
            return GLib.Variant("(s)", (xml + "</node>",))
        if interface == "org.freedesktop.DBus.Properties":
            wanted_interface = parameters.unpack()[0]
            if path == DEVICE and wanted_interface == DEVICE_INTERFACE:
                properties = {"name": GLib.Variant("s", "VELIQEN test"),
                              "type": GLib.Variant("s", "phone"),
                              "isPaired": GLib.Variant("b", self.paired),
                              "isReachable": GLib.Variant("b", self.reachable),
                              "isPairRequestedByPeer": GLib.Variant("b", self.peer_request)}
            elif path == DEVICE + "/battery":
                properties = {"charge": GLib.Variant("i", 73), "isCharging": GLib.Variant("b", True)}
            else:
                raise AssertionError("Unexpected fixture properties path")
            return GLib.Variant("(a{sv})", (properties,))
        expected = self.methods[(path, interface)][method]
        actual = parameters.get_type_string() if parameters else "()"
        if actual != "(" + expected + ")":
            raise AssertionError("Wire signature mismatch: " + actual)
        self.calls.append((path, interface, method, parameters))
        if method == "devices":
            return GLib.Variant("(as)", (["testphone"],))
        if method == "activeConversations":
            return GLib.Variant("(av)", ([self.message()],))
        if method == "sendSms" and self.fail_send:
            raise Gio.DBusError.new_for_dbus_error("org.freedesktop.DBus.Error.AccessDenied", "blocked in isolated test")
        if method == "requestConversation":
            for _sid, (callback, context, data) in list(self.subscriptions.items()):
                source = GLib.idle_source_new()

                def emit(*_args, callback=callback, data=data):
                    for item in (self.message(7), self.message(8, body="Ещё сообщение"),
                                 self.message(99, thread=999)):
                        callback(self, SERVICE, DEVICE, CONVERSATIONS_INTERFACE,
                                 "conversationUpdated", GLib.Variant("(v)", (item,)), data)
                    return False

                source.set_callback(emit)
                source.attach(context)
        return GLib.Variant("()", ())

    def signal_subscribe(self, _sender, _interface, _signal, _path, _arg0, _flags, callback, data):
        self.next_subscription += 1
        self.subscriptions[self.next_subscription] = (callback, GLib.MainContext.get_thread_default(), data)
        return self.next_subscription

    def signal_unsubscribe(self, subscription):
        self.subscriptions.pop(subscription)

    def close(self):
        pass


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon not installed")
class KdeWireTests(unittest.TestCase):
    service_factory = PrivateKdeService

    @classmethod
    def setUpClass(cls):
        cls.service = cls.service_factory()

    @classmethod
    def tearDownClass(cls):
        cls.service.close()

    def setUp(self):
        self.service.calls.clear()
        self.service.paired = True
        self.service.reachable = True
        self.service.peer_request = False
        self.service.fail_send = False
        self.backend = KdeBackend(self.service.client, timeout_ms=1200, signal_wait_ms=120)

    def last(self, method):
        return next(call for call in reversed(self.service.calls) if call[2] == method)

    def test_devices_and_properties(self):
        devices = self.backend.devices()
        self.assertEqual(devices[0]["name"], "VELIQEN test")
        self.assertTrue(devices[0]["paired"])
        self.assertTrue(devices[0]["reachable"])
        self.assertEqual(self.backend.battery("testphone")["charge"], 73)

    def test_pair_accepts_existing_peer_request(self):
        self.service.paired = False
        self.service.peer_request = True
        self.backend.pair("testphone")
        self.assertEqual(self.last("acceptPairing")[0], DEVICE)

    def test_regular_ping_keeps_no_argument_api(self):
        self.backend.ping("testphone")
        parameters = self.last("sendPing")[3]
        self.assertTrue(parameters is None or parameters.get_type_string() == "()")

    def test_message_ping_does_not_silently_fallback_to_regular_ping(self):
        with self.assertRaises(ApiUnavailable):
            self.backend.ping("testphone", "VELIQEN_LINK_SCREEN_REQUEST_V1")
        self.assertFalse(any(call[2] == "sendPing" for call in self.service.calls))

    def test_sms_serializes_variant_of_address_struct_and_int64_sim(self):
        result = self.backend.send_sms("testphone", "+381600000002", "Тест", 2)
        path, interface, _method, parameters = self.last("sendSms")
        self.assertEqual(path, DEVICE + "/sms")
        self.assertEqual(interface, DEVICE_INTERFACE + ".sms")
        self.assertEqual(parameters.get_type_string(), "(avsavx)")
        address = parameters.get_child_value(0).get_child_value(0).get_variant()
        self.assertEqual(address.get_type_string(), "(s)")
        self.assertEqual(address.unpack(), ("+381600000002",))
        self.assertEqual(parameters.get_child_value(3).unpack(), 2)
        self.assertEqual(result["status"], "requested")

    def test_sms_access_denial_is_not_reported_as_sent_or_retried(self):
        self.service.fail_send = True
        with self.assertRaises(KdeError) as caught:
            self.backend.send_sms("testphone", "100", "Тест")
        self.assertEqual(caught.exception.code, "access_denied")
        self.assertEqual(len([call for call in self.service.calls if call[2] == "sendSms"]), 1)

    def test_offline_sms_is_rejected_before_send(self):
        self.service.reachable = False
        with self.assertRaises(KdeError) as caught:
            self.backend.send_sms("testphone", "100", "Тест")
        self.assertEqual(caught.exception.code, "offline")
        self.assertFalse(any(call[2] == "sendSms" for call in self.service.calls))

    def test_conversations_live_on_device_path(self):
        result = self.backend.conversations("testphone")
        self.assertEqual(self.last("activeConversations")[0], DEVICE)
        self.assertEqual(result[0]["thread_id"], 42)
        self.assertEqual(result[0]["addresses"], ["+381600000001"])

    def test_messages_receive_signals_and_do_not_mix_threads_or_same_date_ids(self):
        messages = self.backend.messages("testphone", 42)
        self.assertEqual([message["id"] for message in messages], [7, 8])
        self.assertEqual(self.last("requestConversation")[3].get_type_string(), "(xii)")

    def test_file_uri_is_encoded_for_unicode_spaces_hash_and_percent(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "фото 1 # 20%.txt"
            file.write_text("local test")
            self.backend.share_file("testphone", file)
            uri = self.last("shareUrl")[3].unpack()[0]
            self.assertEqual(uri, file.as_uri())
            self.assertNotIn(" ", uri)
            self.assertIn("%23", uri)
            self.assertIn("%25", uri)

    def test_clipboard_uses_explicit_share_text_without_a_desktop_read(self):
        self.backend.send_clipboard("testphone", "Буфер")
        self.assertEqual(self.last("shareText")[3].unpack(), ("Буфер",))
        self.assertFalse(any(call[2] == "sendClipboard" for call in self.service.calls))

    def test_unsupported_method_fails_before_mutation(self):
        with self.assertRaises(ApiUnavailable):
            self.backend._call(DEVICE, DEVICE_INTERFACE, "notARealMethod", mutating=True)
        self.assertFalse(any(call[2] == "notARealMethod" for call in self.service.calls))

    def test_device_id_path_injection_is_rejected(self):
        with self.assertRaises(KdeError):
            self.backend.device("testphone/../../other")

    def test_invalid_urls_and_directory_fail_before_transfer(self):
        for url in ("file:///etc/passwd", "javascript:alert(1)", "https://", "https://host\nother"):
            with self.assertRaises(KdeError):
                self.backend.share_url("testphone", url)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(KdeError):
                self.backend.share_file("testphone", directory)
        self.assertFalse(any(call[2] == "shareUrl" for call in self.service.calls))

    def test_message_format_failure_is_explicit(self):
        with self.assertRaises(ApiUnavailable):
            decode_message(("unknown", "structure"))


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon not installed")
class LegacySmsTests(unittest.TestCase):
    service_factory = PrivateKdeService

    def test_no_subid_api_uses_verified_conversations_fallback_only_for_default_sim(self):
        service = self.service_factory(modern_sms=False)
        try:
            backend = KdeBackend(service.client, timeout_ms=1200)
            backend.send_sms("testphone", "100", "Тест", -1)
            sends = [call for call in service.calls if call[2] == "sendWithoutConversation"]
            self.assertEqual(len(sends), 1)
            self.assertEqual(sends[0][0], DEVICE)
            self.assertEqual(sends[0][3].get_type_string(), "(avsav)")
            with self.assertRaises(ApiUnavailable):
                backend.send_sms("testphone", "100", "Тест", 2)
            self.assertEqual(len([call for call in service.calls if call[2] == "sendWithoutConversation"]), 1)
        finally:
            service.close()


class KdeVariantTests(KdeWireTests):
    """Runs even where a sandbox refuses all local Unix sockets."""

    __unittest_skip__ = False
    service_factory = VariantTransportService


class LegacyVariantTests(LegacySmsTests):
    __unittest_skip__ = False
    service_factory = VariantTransportService


@unittest.skipUnless(shutil.which("dbus-daemon"), "dbus-daemon not installed")
class PingMessageWireTests(unittest.TestCase):
    service_factory = PrivateKdeService

    def test_message_ping_sends_exact_string_variant(self):
        service = self.service_factory(message_ping=True)
        try:
            backend = KdeBackend(service.client, timeout_ms=1200)
            message = "VELIQEN_LINK_SCREEN_REQUEST_V1"
            backend.ping("testphone", message)
            sends = [call for call in service.calls if call[2] == "sendPing"]
            self.assertEqual(len(sends), 1)
            self.assertEqual(sends[0][0], DEVICE + "/ping")
            self.assertEqual(sends[0][3].get_type_string(), "(s)")
            self.assertEqual(sends[0][3].unpack(), (message,))
        finally:
            service.close()


class PingMessageVariantTests(PingMessageWireTests):
    __unittest_skip__ = False
    service_factory = VariantTransportService


if __name__ == "__main__":
    unittest.main()
