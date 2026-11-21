"""User-selected, reversible HFP audio routing for PipeWire.

Prefers native PipeWire ports (including the stream nodes used for phone HFP).
Falls back to two Pulse-compatible loopback modules only when the phone really
exposes a matched HFP source and sink. Existing unrelated links are untouched.
"""
from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import threading
import time
import uuid

from .telephony import TelephonyManager, TelephonyError, normalize_address


class AudioError(RuntimeError):
    pass


def _props(obj):
    return obj.get("info", {}).get("props", {}) or obj.get("properties", {})


def object_address(obj):
    props = _props(obj)
    for value in (props.get("api.bluez5.address"), props.get("device.string")):
        if value:
            try:
                return normalize_address(str(value))
            except TelephonyError:
                pass
    for value in (props.get("api.bluez5.path", ""), obj.get("name", ""), props.get("node.name", ""), props.get("device.name", "")):
        match = re.search(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{2}(?:[_:][0-9A-Fa-f]{2}){5})(?![0-9A-Fa-f])", str(value))
        if match:
            return normalize_address(match.group(1))
    return None


def _hfp(obj):
    props = _props(obj)
    profile = str(props.get("api.bluez5.profile", props.get("bluetooth.protocol", ""))).lower()
    return profile in ("headset-audio-gateway", "hfp-ag", "hfp_ag", "handsfree_audio_gateway", "audio-gateway")


def _node_item(obj):
    props = _props(obj)
    return {"name": str(props.get("node.name", "")),
            "description": str(props.get("node.description") or props.get("node.nick") or props.get("node.name") or "Аудиоустройство"),
            "id": obj["id"], "address": object_address(obj), "phone_gateway": _hfp(obj)}


class AudioRouter:
    def __init__(self, telephony=None, runner=None, which=None):
        self._telephony = telephony or TelephonyManager()
        self._run = runner or subprocess.run
        self._which = which or shutil.which
        self._lock = threading.RLock()
        self._token = uuid.uuid4().hex
        self._links = []
        self._modules = []
        self._old_profiles = {}
        self._active_pairs = set()
        self._active_backend = None
        self._active_since = None
        self.active = False
        self.address = None

    def _command(self, name, args):
        executable = self._which(name)
        if not executable:
            raise AudioError(f"Не найдена программа {name}. Установите зависимости через INSTALL-UBUNTU.sh.")
        try:
            result = self._run([executable, *args], text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, timeout=6, check=False)
        except subprocess.TimeoutExpired as exc:
            raise AudioError("Звуковая служба не ответила за 6 секунд.") from exc
        except OSError as exc:
            raise AudioError("Не удалось обратиться к звуковой службе.") from exc
        if result.returncode:
            detail = (result.stderr or "").strip()[-400:]
            raise AudioError(detail or f"Команда {name} не выполнена.")
        return result.stdout or ""

    def _json(self, name, args):
        raw = self._command(name, args)
        if len(raw) > 16 * 1024 * 1024:
            raise AudioError("Ответ звуковой службы слишком большой.")
        try:
            result = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise AudioError("Звуковая служба вернула неподдерживаемый формат данных.") from exc
        if not isinstance(result, list):
            raise AudioError("Неподдерживаемый ответ звуковой службы.")
        return result

    def _graph(self):
        return self._json("pw-dump", [])

    def _pulse(self, kind):
        return self._json("pactl", ["--format=json", "list", kind])

    def _paired(self, address):
        try:
            address = normalize_address(address)
            device = next((d for d in self._telephony.bluetooth_devices()
                           if d["address"] == address and d.get("paired") and d.get("connected")), None)
        except TelephonyError as exc:
            raise AudioError(str(exc)) from exc
        if not device:
            raise AudioError("Выбранный телефон не подключен или не сопряжен по Bluetooth.")
        return address

    @staticmethod
    def _graph_items(graph, address=None):
        result = {"local_sources": [], "local_sinks": [], "hfp_sources": [], "hfp_sinks": []}
        for obj in graph:
            if obj.get("type") != "PipeWire:Interface:Node":
                continue
            props = _props(obj)
            media = str(props.get("media.class", ""))
            item = _node_item(obj)
            if not item["name"]:
                continue
            remote = object_address(obj)
            if address and remote == address and _hfp(obj):
                if media in ("Audio/Source", "Stream/Output/Audio"):
                    result["hfp_sources"].append(item)
                elif media in ("Audio/Sink", "Stream/Input/Audio"):
                    result["hfp_sinks"].append(item)
            elif remote != address or not address:
                if media == "Audio/Source" and not item["name"].endswith(".monitor"):
                    result["local_sources"].append(item)
                elif media == "Audio/Sink":
                    result["local_sinks"].append(item)
        return result

    def snapshot(self, address=None):
        with self._lock:
            if self.active:
                self.reconcile_calls(self._telephony.refresh())
            result = {"available": False, "status": "", "active": self.active, "address": self.address,
                      "local_sources": [], "local_sinks": [], "hfp_sources": [], "hfp_sinks": []}
            if address:
                try:
                    address = self._paired(address)
                except AudioError as exc:
                    result["status"] = str(exc)
                    address = None
            try:
                if self._which("pw-dump") and self._which("pw-link"):
                    graph = self._graph()
                    result.update(self._graph_items(graph, address))
                    if self.active and self._active_backend == "pipewire":
                        actual = {(out, inp) for _, out, inp in self._links_in(graph)}
                        if not self._active_pairs.issubset(actual):
                            self._cleanup_routes()
                            result["active"] = self.active
                            result["status"] = "Аудиосвязь прервалась. Обновите устройства и подключите звук снова."
                else:
                    for kind, key in (("sources", "local_sources"), ("sinks", "local_sinks")):
                        for obj in self._pulse(kind):
                            item = {"name": obj["name"], "description": obj.get("description", obj["name"]),
                                    "address": object_address(obj), "phone_gateway": _hfp(obj)}
                            if address and object_address(obj) == address and _hfp(obj):
                                result["hfp_sources" if kind == "sources" else "hfp_sinks"].append(item)
                            elif object_address(obj) != address and not obj["name"].endswith(".monitor"):
                                result[key].append(item)
                result["available"] = bool(address and result["hfp_sources"] and result["hfp_sinks"])
                if self.active:
                    result["status"] = "Маршрут звука подключен. Проверьте слышимость в обе стороны."
                elif result["available"]:
                    result["status"] = "HFP-звук доступен. Выберите микрофон и динамики и нажмите «Подключить звук»."
                elif not result["status"]:
                    result["status"] = "HFP-звук пока не готов. Начните или примите звонок, выберите Bluetooth на телефоне и обновите список."
            except AudioError as exc:
                result["status"] = str(exc)
            return result

    def default_devices(self, snapshot=None):
        """Select the user's current system defaults without changing routes.

        Only names in the current local-device inventory are eligible. If the
        system default points at the phone itself, do not create a feedback
        loop; use an unambiguous sole local device or ask for a selection.
        """
        state = snapshot if snapshot is not None else self.snapshot()
        result = {"source": None, "sink": None, "complete": False}
        for kind, key in (("source", "local_sources"), ("sink", "local_sinks")):
            choices = [item for item in state.get(key, []) if not item.get("phone_gateway")]
            names = {item["name"] for item in choices}
            default = None
            if self._which("pactl"):
                try:
                    candidate = self._command("pactl", ["get-default-" + kind]).strip()
                    if candidate in names:
                        default = candidate
                except AudioError:
                    pass
            if default is None and len(names) == 1:
                default = next(iter(names))
            result[kind] = default
        result["complete"] = bool(result["source"] and result["sink"])
        return result

    def prepare(self, address):
        """Explicitly select only this phone's gateway profile, remembering it."""
        with self._lock:
            address = self._paired(address)
            cards = [c for c in self._pulse("cards") if object_address(c) == address]
            if len(cards) != 1:
                raise AudioError("Звуковая карта телефона не найдена однозначно. Подключите HFP в настройках Bluetooth.")
            card = cards[0]
            profiles = card.get("profiles", {})
            if isinstance(profiles, list):
                profiles = {p["name"]: p for p in profiles}
            preferred = [p for p in ("audio-gateway", "headset-audio-gateway")
                         if p in profiles and profiles[p].get("available") not in ("no", False)]
            if not preferred:
                raise AudioError("У телефона нет доступного профиля HFP Audio Gateway. Обычного A2DP-подключения недостаточно.")
            current = card.get("active_profile", "")
            if isinstance(current, dict):
                current = current.get("name", "")
            target = preferred[0]
            if current != target:
                self._command("pactl", ["set-card-profile", card["name"], target])
                self._old_profiles.setdefault(card["name"], (address, current, target))
            return {"status": "Профиль звонков выбран. Начните разговор и обновите аудиоустройства.", "profile": target}

    @staticmethod
    def _ports(graph, node_id, direction):
        ports = []
        for obj in graph:
            if obj.get("type") != "PipeWire:Interface:Port":
                continue
            props = _props(obj)
            if str(props.get("node.id")) == str(node_id) and props.get("port.direction") == direction:
                if props.get("port.monitor") in (True, "true"):
                    continue
                ports.append(obj)
        return sorted(ports, key=lambda p: (str(_props(p).get("audio.channel", "")), p["id"]))

    @staticmethod
    def _links_in(graph):
        result = []
        for obj in graph:
            if obj.get("type") != "PipeWire:Interface:Link":
                continue
            info, props = obj.get("info", {}), _props(obj)
            out = info.get("output-port-id", props.get("link.output.port"))
            inp = info.get("input-port-id", props.get("link.input.port"))
            if out is not None and inp is not None:
                result.append((obj, int(out), int(inp)))
        return result

    @staticmethod
    def _pairs(outputs, inputs):
        if not outputs or not inputs:
            raise AudioError("У HFP-устройства еще нет активных аудиопортов. Выберите Bluetooth во время звонка и повторите.")
        # HFP is commonly mono. Fan its one output to both speaker channels;
        # for microphone stereo -> mono select a single capture channel to
        # avoid doubling gain. A stereo mix would require a mixing node.
        if len(outputs) == 1:
            return [(outputs[0]["id"], i["id"]) for i in inputs]
        if len(inputs) == 1:
            return [(outputs[0]["id"], inputs[0]["id"])]
        pairs = []
        for inp in inputs:
            channel = _props(inp).get("audio.channel")
            output = next((o for o in outputs if channel and _props(o).get("audio.channel") == channel), outputs[min(len(pairs), len(outputs)-1)])
            pairs.append((output["id"], inp["id"]))
        return pairs

    def _start_graph(self, address, local_source, local_sink):
        graph = self._graph()
        items = self._graph_items(graph, address)
        if len(items["hfp_sources"]) != 1 or len(items["hfp_sinks"]) != 1:
            raise AudioError("Для выбранного телефона не найдена одна полная пара HFP-аудиоузлов. Начните звонок и выберите Bluetooth на телефоне.")
        source = next((i for i in items["local_sources"] if i["name"] == local_source), None)
        sink = next((i for i in items["local_sinks"] if i["name"] == local_sink), None)
        if not source or not sink:
            raise AudioError("Выбранные микрофон или динамики отключены. Обновите аудиоустройства.")
        remote_source, remote_sink = items["hfp_sources"][0], items["hfp_sinks"][0]
        outgoing = self._pairs(self._ports(graph, source["id"], "out"), self._ports(graph, remote_sink["id"], "in"))
        incoming = self._pairs(self._ports(graph, remote_source["id"], "out"), self._ports(graph, sink["id"], "in"))
        wanted = set(outgoing + incoming)
        links = self._links_in(graph)
        existing = {(out, inp) for _, out, inp in links}
        remote_out = {p["id"] for p in self._ports(graph, remote_source["id"], "out")}
        remote_in = {p["id"] for p in self._ports(graph, remote_sink["id"], "in")}
        if any((out in remote_out or inp in remote_in) and (out, inp) not in wanted for _, out, inp in links):
            raise AudioError("PipeWire уже связал телефон с другим микрофоном или выходом. Выберите те же устройства в Ubuntu либо отключите старый маршрут в звуковом микшере. Чужие связи не изменены.")
        created = []
        try:
            for out, inp in sorted(wanted - existing):
                props = json.dumps({"veliqen.link.owner": self._token, "object.linger": True})
                self._command("pw-link", ["--props=" + props, str(out), str(inp)])
                created.append((out, inp))
            after = self._links_in(self._graph())
            for obj, out, inp in after:
                if (out, inp) in created and _props(obj).get("veliqen.link.owner") == self._token:
                    self._links.append({"id": obj["id"], "serial": _props(obj).get("object.serial"),
                                        "out": out, "in": inp})
            if not wanted.issubset({(o, i) for _, o, i in after}):
                raise AudioError("PipeWire не подтвердил все аудиосоединения. Маршрут отменен.")
            self._active_pairs = wanted
            return "pipewire"
        except Exception:
            # Also find tagged links if an intermediate command failed before
            # the normal enumeration; never delete untagged/pre-existing links.
            try:
                known = {entry["id"] for entry in self._links}
                for obj, out, inp in self._links_in(self._graph()):
                    if _props(obj).get("veliqen.link.owner") == self._token and obj["id"] not in known:
                        self._links.append({"id": obj["id"], "serial": _props(obj).get("object.serial"), "out": out, "in": inp})
            finally:
                self._cleanup_routes()
            raise

    def _start_pulse(self, address, local_source, local_sink):
        sources, sinks = self._pulse("sources"), self._pulse("sinks")
        remote_sources = [x for x in sources if object_address(x) == address and _hfp(x)]
        remote_sinks = [x for x in sinks if object_address(x) == address and _hfp(x)]
        if len(remote_sources) != 1 or len(remote_sinks) != 1:
            raise AudioError("HFP-источник и приемник телефона не появились. Нужны pw-dump и pw-link для этой конфигурации PipeWire.")
        if not any(x["name"] == local_source and object_address(x) != address and not x["name"].endswith(".monitor") for x in sources):
            raise AudioError("Выберите действующий микрофон ноутбука.")
        if not any(x["name"] == local_sink and object_address(x) != address for x in sinks):
            raise AudioError("Выберите действующие динамики или наушники ноутбука.")
        pairs = [(remote_sources[0]["name"], local_sink), (local_source, remote_sinks[0]["name"])]
        if any(not re.fullmatch(r"[A-Za-z0-9_.:/\-]+", name) for pair in pairs for name in pair):
            raise AudioError("Имена аудиоустройств требуют маршрутизации через pw-link.")
        try:
            for source, sink in pairs:
                module = self._command("pactl", ["load-module", "module-loopback", "source=" + source,
                                      "sink=" + sink, "latency_msec=60", "source_dont_move=true", "sink_dont_move=true",
                                      "source_output_properties=application.name=VELIQEN-Link-" + self._token]).strip()
                if not module.isdigit():
                    raise AudioError("PipeWire не вернул номер созданного аудиомодуля.")
                self._modules.append(int(module))
        except Exception:
            self._cleanup_routes()
            raise
        return "pulse-loopback"

    def start(self, address, local_source, local_sink):
        with self._lock:
            address = self._paired(address)
            calls = self._telephony.refresh().get("calls", [])
            if not any(call.get("address") == address and call.get("state") == "active" for call in calls):
                raise AudioError("Сначала примите звонок или дождитесь ответа собеседника. Микрофон подключается только для активного разговора.")
            if not local_source or not local_sink:
                raise AudioError("Выберите микрофон и динамики.")
            if self.active or self._links or self._modules:
                errors = self._cleanup_routes()
                if errors or self._links or self._modules:
                    raise AudioError("Не удалось убрать прежний маршрут; новый не создан. Нажмите «Отключить маршрут» и повторите.")
            self.address = address
            if self._which("pw-dump") and self._which("pw-link"):
                backend = self._start_graph(address, local_source, local_sink)
            else:
                backend = self._start_pulse(address, local_source, local_sink)
            self.active = True
            self._active_backend = backend
            self._active_since = time.monotonic()
            return {"active": True, "address": address, "backend": backend,
                    "status": "Маршрут звука подключен. Проверьте слышимость в обе стороны."}

    def _cleanup_routes(self):
        errors = []
        if self._links:
            try:
                graph = {obj["id"]: obj for obj in self._graph()}
                pending = []
                for entry in self._links:
                    obj = graph.get(entry["id"])
                    # Object IDs can be reused after devices reconnect.  Both
                    # the session tag and serial must match before deletion.
                    if not obj or _props(obj).get("veliqen.link.owner") != self._token:
                        continue
                    if entry["serial"] is not None and _props(obj).get("object.serial") != entry["serial"]:
                        continue
                    try:
                        self._command("pw-link", ["--disconnect", str(entry["id"])])
                    except AudioError as exc:
                        pending.append(entry)
                        errors.append(str(exc))
                self._links = pending
            except AudioError as exc:
                errors.append(str(exc))
        if self._modules:
            try:
                modules = {int(m["index"]): m for m in self._pulse("modules")}
                pending = []
                for module_id in self._modules:
                    module = modules.get(module_id)
                    if not module or module.get("name") != "module-loopback" or self._token not in module.get("argument", ""):
                        continue
                    try:
                        self._command("pactl", ["unload-module", str(module_id)])
                    except AudioError as exc:
                        pending.append(module_id)
                        errors.append(str(exc))
                self._modules = pending
            except AudioError as exc:
                errors.append(str(exc))
        self.active = bool(self._links or self._modules)
        if not self.active:
            self.address = None
            self._active_pairs = set()
            self._active_backend = None
            self._active_since = None
        return errors

    def reconcile_calls(self, telephony_state):
        """Stop owned audio on hangup/disconnect; accepts refresh()'s result.

        The UI should call this on its periodic call refresh, including when
        the Calls tab is not visible. snapshot() also performs this check.
        Only observations begun after this route became active may stop it.
        Both timestamps use time.monotonic(), never wall-clock time.
        """
        with self._lock:
            if not self.active:
                return {"active": False, "status": ""}
            sampled_at = telephony_state.get("snapshot_monotonic")
            now = time.monotonic()
            valid_stamp = (type(sampled_at) in (int, float)
                           and math.isfinite(sampled_at) and 0 <= sampled_at <= now)
            if not valid_stamp:
                # Error/legacy callers can supply an empty state without a
                # clock. Re-read live under the audio lock before deciding;
                # never treat an undated queued state as current evidence.
                try:
                    telephony_state = self._telephony.refresh()
                except Exception:
                    return self.stop()
            elif self._active_since is not None and sampled_at < self._active_since:
                return {"active": True, "status": "", "stale_snapshot_ignored": True}
            if not any(call.get("address") == self.address and call.get("state") == "active"
                       for call in telephony_state.get("calls", [])):
                return self.stop()
            return {"active": self.active, "status": ""}

    def stop(self):
        with self._lock:
            errors = self._cleanup_routes()
            if self._old_profiles and not self.active:
                try:
                    cards = {c["name"]: c for c in self._pulse("cards")}
                    pending = {}
                    for name, (address, previous, selected) in self._old_profiles.items():
                        card = cards.get(name)
                        current = card.get("active_profile") if card else None
                        if isinstance(current, dict):
                            current = current.get("name")
                        # Do not overwrite a subsequent user profile change.
                        if card and object_address(card) == address and current == selected and previous:
                            try:
                                self._command("pactl", ["set-card-profile", name, previous])
                            except AudioError as exc:
                                pending[name] = (address, previous, selected)
                                errors.append(str(exc))
                    self._old_profiles = pending
                except AudioError as exc:
                    errors.append(str(exc))
            return {"active": self.active, "status": "Созданные VELIQEN Link аудиосвязи отключены." if not errors
                    else "Не все аудиосвязи удалось убрать: " + "; ".join(errors), "errors": errors}

    close = stop
