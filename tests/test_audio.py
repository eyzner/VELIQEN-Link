import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "linux"))
from veliqen_link.audio import AudioRouter, AudioError

ADDRESS = "AA:BB:CC:DD:EE:FF"


class FakePhone:
    connected = True
    active_call = True

    def bluetooth_devices(self):
        return [{"address": ADDRESS, "paired": True, "connected": self.connected}]

    def refresh(self):
        return {"calls": [{"address": ADDRESS, "state": "active"}] if self.active_call else [],
                "snapshot_monotonic": time.monotonic()}


def node(ident, name, media, remote=False):
    props = {"node.name": name, "node.description": name, "media.class": media}
    if remote:
        props.update({"api.bluez5.address": ADDRESS, "api.bluez5.profile": "headset-audio-gateway"})
    return {"id": ident, "type": "PipeWire:Interface:Node", "info": {"props": props}}


def port(ident, node_id, direction, channel="MONO"):
    return {"id": ident, "type": "PipeWire:Interface:Port", "info": {"props": {
        "node.id": node_id, "port.direction": direction, "audio.channel": channel}}}


def link(ident, output, input_, props=None):
    return {"id": ident, "type": "PipeWire:Interface:Link", "info": {
        "output-port-id": output, "input-port-id": input_, "props": props or {"object.serial": ident + 1000}}}


class FakeGraph:
    def __init__(self):
        self.graph = [node(1, "mic", "Audio/Source"), node(2, "speaker", "Audio/Sink"),
                      node(3, "phone_rx", "Stream/Output/Audio", True), node(4, "phone_tx", "Stream/Input/Audio", True),
                      port(11, 1, "out"), port(21, 2, "in", "FL"), port(22, 2, "in", "FR"),
                      port(31, 3, "out"), port(41, 4, "in"), link(800, 501, 601)]
        self.commands = []
        self.next_id = 900
        self.fail_after = None
        self.created = 0

    def __call__(self, args, **kwargs):
        self.commands.append(args)
        if args[0].endswith("pw-dump"):
            return subprocess.CompletedProcess(args, 0, json.dumps(self.graph), "")
        if args[0].endswith("pw-link") and args[1] == "--disconnect":
            self.graph = [o for o in self.graph if o["id"] != int(args[2])]
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0].endswith("pw-link") and args[1].startswith("--props="):
            if self.fail_after is not None and self.created >= self.fail_after:
                return subprocess.CompletedProcess(args, 1, "", "fake failure")
            self.created += 1
            props = json.loads(args[1].split("=", 1)[1])
            props["object.serial"] = self.next_id + 1000
            self.graph.append(link(self.next_id, int(args[2]), int(args[3]), props))
            self.next_id += 1
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(args)


class AudioTests(unittest.TestCase):
    def setUp(self):
        self.phone, self.graph = FakePhone(), FakeGraph()
        self.router = AudioRouter(telephony=self.phone, runner=self.graph, which=lambda name: "/usr/bin/" + name)

    def test_real_bidirectional_port_plan_and_owned_cleanup(self):
        self.assertTrue(self.router.snapshot(ADDRESS)["available"])
        result = self.router.start(ADDRESS, "mic", "speaker")
        self.assertTrue(result["active"])
        created = [o for o in self.graph.graph if o["id"] >= 900]
        pairs = {(o["info"]["output-port-id"], o["info"]["input-port-id"]) for o in created}
        self.assertEqual(pairs, {(11, 41), (31, 21), (31, 22)})
        self.router.stop()
        self.assertFalse(self.router.active)
        self.assertTrue(any(o["id"] == 800 for o in self.graph.graph))
        self.assertFalse(any(o["id"] >= 900 for o in self.graph.graph))

    def test_no_wrong_phone_or_unpaired_audio_route(self):
        self.phone.connected = False
        with self.assertRaises(AudioError):
            self.router.start(ADDRESS, "mic", "speaker")
        self.assertEqual(self.graph.commands, [])

    def test_microphone_requires_an_active_call_and_stops_on_hangup(self):
        self.phone.active_call = False
        with self.assertRaises(AudioError):
            self.router.start(ADDRESS, "mic", "speaker")
        self.phone.active_call = True
        self.router.start(ADDRESS, "mic", "speaker")
        self.phone.active_call = False
        self.router.reconcile_calls(self.phone.refresh())
        self.assertFalse(self.router.active)
        self.assertFalse(any(o["id"] >= 900 for o in self.graph.graph))

    def test_delayed_pre_call_snapshot_does_not_disconnect_new_route(self):
        # Reproduce the GUI race: an idle refresh finishes, the user answers
        # and starts audio, then GTK delivers the earlier empty result.
        self.phone.active_call = False
        delayed_empty_state = self.phone.refresh()
        self.phone.active_call = True
        self.router.start(ADDRESS, "mic", "speaker")
        before = {o["id"] for o in self.graph.graph if o["id"] >= 900}
        self.assertTrue(before)
        result = self.router.reconcile_calls(delayed_empty_state)
        self.assertTrue(result.get("stale_snapshot_ignored"))
        self.assertTrue(self.router.active)
        self.assertEqual(before, {o["id"] for o in self.graph.graph if o["id"] >= 900})
        # A later genuine hangup must still clean up, not disable cleanup.
        self.phone.active_call = False
        self.router.reconcile_calls(self.phone.refresh())
        self.assertFalse(self.router.active)
        self.assertFalse(any(o["id"] >= 900 for o in self.graph.graph))

    def test_undated_error_snapshot_gets_live_check_before_stopping_audio(self):
        self.router.start(ADDRESS, "mic", "speaker")
        self.router.reconcile_calls({"available": False, "calls": []})
        self.assertTrue(self.router.active)
        self.phone.active_call = False
        self.router.reconcile_calls({"available": False, "calls": []})
        self.assertFalse(self.router.active)

    def test_other_phone_cannot_be_substituted(self):
        for obj in self.graph.graph:
            if obj["type"] == "PipeWire:Interface:Node" and obj["id"] in (3, 4):
                obj["info"]["props"]["api.bluez5.address"] = "00:11:22:33:44:55"
        with self.assertRaises(AudioError):
            self.router.start(ADDRESS, "mic", "speaker")
        self.assertFalse(any(c[0].endswith("pw-link") for c in self.graph.commands))

    def test_partial_failure_removes_only_created_links(self):
        self.graph.fail_after = 1
        with self.assertRaises(AudioError):
            self.router.start(ADDRESS, "mic", "speaker")
        self.assertTrue(any(o["id"] == 800 for o in self.graph.graph))
        self.assertFalse(any(o["id"] >= 900 for o in self.graph.graph))

    def test_existing_mic_route_is_not_silently_mixed_or_removed(self):
        self.graph.graph.append(link(801, 999, 41))
        with self.assertRaises(AudioError):
            self.router.start(ADDRESS, "mic", "speaker")
        self.assertTrue(any(o["id"] == 801 for o in self.graph.graph))
        self.assertEqual(self.graph.created, 0)

    def test_reused_link_id_is_not_deleted(self):
        self.router.start(ADDRESS, "mic", "speaker")
        changed = next(o for o in self.graph.graph if o["id"] == 900)
        changed["info"]["props"] = {"object.serial": 99999, "another.owner": "user"}
        self.router.stop()
        self.assertTrue(any(o["id"] == 900 for o in self.graph.graph))


if __name__ == "__main__":
    unittest.main()
