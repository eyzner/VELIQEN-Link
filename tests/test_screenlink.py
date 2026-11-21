"""Real mutually authenticated TLS sessions with isolated KDE-style identities."""
from pathlib import Path
import hashlib
import io
import json
import os
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.screenlink import (MAX_FRAME, MAX_JSON, ScreenLinkError, ScreenReceiver,
                                    encode_json, read_packet, trusted_certificates,
                                    validate_command)


class BufferSocket:
    def __init__(self, data): self.buffer = io.BytesIO(data)
    def recv(self, count): return self.buffer.read(min(count, 3))


class ProtocolTests(unittest.TestCase):
    def test_fragmented_json(self):
        self.assertEqual(read_packet(BufferSocket(encode_json({"op": "back"}))), (0, {"op": "back"}))

    def test_frame_limit_before_allocation(self):
        with self.assertRaises(ScreenLinkError):
            read_packet(BufferSocket(struct.pack("!I", MAX_FRAME + 2)))

    def test_json_limit(self):
        with self.assertRaises(ScreenLinkError):
            read_packet(BufferSocket(struct.pack("!I", MAX_JSON + 2) + b"\0"))

    def test_bad_json_and_jpeg(self):
        for data in [struct.pack("!I", 3) + b"\0[]", struct.pack("!I", 4) + b"\x01abc"]:
            with self.subTest(data=data), self.assertRaises(ScreenLinkError):
                read_packet(BufferSocket(data))

    def test_input_bounds_and_no_arbitrary_actions(self):
        for command in [{"op": "shell", "text": "anything"}, {"op": "tap", "x": -1, "y": 0},
                        {"op": "tap", "x": float("nan"), "y": 0}, {"op": "tap", "x": True, "y": 0},
                        {"op": "text", "text": "a" * 1025}]:
            with self.subTest(command=command), self.assertRaises(ScreenLinkError): validate_command(command)
        self.assertEqual(validate_command({"op": "swipe", "x": 0, "y": 0, "x2": 1, "y2": 1, "duration": 9000})["duration"], 1000)


class TlsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="veliqen-tls-test-")
        cls.base = Path(cls.temp.name)
        for name in ["computer", "phone", "stranger"]:
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                            "-keyout", str(cls.base / (name + ".key")), "-out", str(cls.base / (name + ".pem")),
                            "-days", "1", "-subj", "/CN=" + name,
                            "-addext", "basicConstraints=critical,CA:FALSE"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="veliqen-trust-test-")
        self.config = Path(self.directory.name)
        (self.config / "privateKey.pem").write_bytes((self.base / "computer.key").read_bytes())
        (self.config / "certificate.pem").write_bytes((self.base / "computer.pem").read_bytes())
        self.write_trust()
        self.sessions = []
        self.frames = []
        self.frame_seen = threading.Event()
        self.ended = threading.Event()
        self.receiver = ScreenReceiver(on_session=self.sessions.append,
            on_frame=lambda value, session_id: (self.frames.append(value), self.frame_seen.set()),
            on_end=lambda value, session_id: self.ended.set(), config_dir=self.config, host="127.0.0.1", port=0)
        self.receiver.set_allowed_devices(["phone"])
        self.receiver.start()
        self.clients = []

    def write_trust(self, enabled=True):
        value = (self.base / "phone.pem").read_text().replace("\n", "\\n")
        (self.config / "trusted_devices").write_text('[phone]\nname=Test phone\ncertificate="' + value + '"\n' if enabled else "")

    def tearDown(self):
        self.receiver.close()
        for client in self.clients:
            try: client.close()
            except OSError: pass
        self.directory.cleanup()

    def connect(self, name="phone", claimed="phone", control=False):
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_verify_locations(self.base / "computer.pem")
        context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        if name:
            context.load_cert_chain(self.base / (name + ".pem"), self.base / (name + ".key"))
        raw = socket.create_connection(("127.0.0.1", self.receiver.port), timeout=3)
        client = context.wrap_socket(raw, server_hostname="computer")
        self.clients.append(client)
        client.sendall(encode_json({"type": "hello", "protocol": 1, "deviceId": claimed, "name": "Phone", "control": control}))
        return client

    def test_qsettings_cert_quoted_newlines(self):
        certs = trusted_certificates(self.config)
        self.assertEqual(set(certs), {"phone"})
        der = ssl.PEM_cert_to_DER_cert((self.base / "phone.pem").read_text())
        self.assertEqual(certs["phone"][1], hashlib.sha256(der).digest())

    def test_paired_session_and_frame(self):
        client = self.connect()
        self.assertEqual(read_packet(client)[1]["op"], "ready")
        frame = b"\xff\xd8test\xff\xd9"
        client.sendall(struct.pack("!I", len(frame) + 1) + b"\1" + frame)
        self.assertTrue(self.frame_seen.wait(3))
        self.assertEqual(self.frames, [frame])
        self.assertEqual(self.sessions[0]["device_id"], "phone")

    def test_unknown_certificate_rejected(self):
        with self.assertRaises((ssl.SSLError, OSError, EOFError)):
            read_packet(self.connect("stranger"))
        self.assertEqual(self.frames, [])
        self.assertEqual(self.sessions, [])

    def test_certificate_required(self):
        with self.assertRaises((ssl.SSLError, OSError, EOFError)):
            read_packet(self.connect(None))
        self.assertEqual(self.sessions, [])

    def test_claimed_id_cannot_override_certificate(self):
        with self.assertRaises((ssl.SSLError, OSError, EOFError)):
            read_packet(self.connect(claimed="someone-else"))
        self.assertEqual(self.sessions, [])

    def test_unselected_device_rejected(self):
        self.receiver.set_allowed_devices([])
        with self.assertRaises((ssl.SSLError, OSError, EOFError)):
            read_packet(self.connect())
        self.assertEqual(self.sessions, [])

    def test_revoke_ends_session_before_more_frames(self):
        client = self.connect()
        read_packet(client)
        self.write_trust(enabled=False)
        frame = b"\xff\xd8test\xff\xd9"
        client.sendall(struct.pack("!I", len(frame) + 1) + b"\1" + frame)
        self.assertTrue(self.ended.wait(3))
        self.assertEqual(self.frames, [])

    def test_control_requires_phone_opt_in(self):
        client = self.connect(control=False)
        read_packet(client)
        with self.assertRaises(ScreenLinkError): self.receiver.send_command({"op": "home"})

    def test_authorized_control_stays_inside_tls(self):
        client = self.connect(control=True)
        read_packet(client)
        self.receiver.send_command({"op": "tap", "x": 0.25, "y": 0.75})
        self.assertEqual(read_packet(client), (0, {"op": "tap", "x": 0.25, "y": 0.75}))

    def test_replaced_certificate_blocks_input_before_next_frame(self):
        client = self.connect(control=True)
        read_packet(client)
        value = (self.base / "stranger.pem").read_text().replace("\n", "\\n")
        (self.config / "trusted_devices").write_text('[phone]\ncertificate="' + value + '"\n')
        with self.assertRaises(ScreenLinkError): self.receiver.send_command({"op": "home"})
        with self.assertRaises((EOFError, OSError)): read_packet(client)
        self.assertTrue(self.ended.wait(3))

    def test_change_selected_phone_closes_session(self):
        client = self.connect()
        read_packet(client)
        self.receiver.set_allowed_devices(["other"])
        with self.assertRaises((EOFError, OSError)): read_packet(client)
        self.assertTrue(self.ended.wait(3))

    def test_late_old_end_is_identified_and_cannot_stop_new_session(self):
        old_end_started, release_old_end = threading.Event(), threading.Event()
        ended_ids = []
        def delayed_end(message, session_id):
            old_end_started.set()
            release_old_end.wait(3)
            ended_ids.append(session_id)
        self.receiver.on_end = delayed_end
        first = self.connect(control=True)
        read_packet(first)
        old_id = self.receiver.snapshot()["session"]["session_id"]
        first.shutdown(socket.SHUT_RDWR)
        first.close()
        self.assertTrue(old_end_started.wait(3))
        second = self.connect(control=True)
        read_packet(second)
        new_id = self.receiver.snapshot()["session"]["session_id"]
        self.assertNotEqual(old_id, new_id)
        self.receiver.stop_session(session_id=old_id)
        self.assertEqual(self.receiver.snapshot()["session"]["session_id"], new_id)
        with self.assertRaises(ScreenLinkError):
            self.receiver.send_command({"op": "home"}, session_id=old_id)
        self.receiver.send_command({"op": "home"}, session_id=new_id)
        self.assertEqual(read_packet(second)[1], {"op": "home"})
        release_old_end.set()


if __name__ == "__main__": unittest.main()
