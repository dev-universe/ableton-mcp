"""Regression tests for the Live-side Remote Script's TCP surface.

The script normally runs inside Live's interpreter, so ``_Framework`` is
stubbed and the class is instantiated without ``__init__`` (which would open a
socket). No Ableton, no network.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REPO_SCRIPT = REPO_ROOT / "AbletonMCP_Remote_Script" / "__init__.py"
BUNDLED_SCRIPT = (REPO_ROOT / "MCP_Server" / "bundled_ableton_remote_script"
                  / "AbletonMCP_init.py")


def _load_script_module(fresh=False):
    """Import the Live-side script with _Framework stubbed out.

    Each call re-executes the module, so ``fresh`` is only a readability hint
    at call sites that depend on module-level state being re-evaluated (the
    ABLETON_MCP_HOST lookup).
    """
    framework = types.ModuleType("_Framework")
    control_surface_mod = types.ModuleType("_Framework.ControlSurface")

    class _StubControlSurface(object):
        def __init__(self, *args, **kwargs):
            pass

        def log_message(self, *args, **kwargs):
            pass

        def show_message(self, *args, **kwargs):
            pass

    control_surface_mod.ControlSurface = _StubControlSurface
    framework.ControlSurface = control_surface_mod
    sys.modules.setdefault("_Framework", framework)
    sys.modules.setdefault("_Framework.ControlSurface", control_surface_mod)

    spec = importlib.util.spec_from_file_location(
        "ableton_mcp_remote_script_under_test", BUNDLED_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def script_module():
    return _load_script_module()


def make_instance(script_module, application=None):
    inst = script_module.AbletonMCP.__new__(script_module.AbletonMCP)
    inst.application = lambda: application
    inst.log_message = lambda *a, **k: None
    inst.show_message = lambda *a, **k: None
    return inst


# ------------------------------------------------------------------ binding

def test_socket_binds_loopback_by_default(script_module):
    """The command surface is unauthenticated: it must not listen publicly
    unless the user opts in via ABLETON_MCP_HOST."""
    assert script_module.HOST == "127.0.0.1"


def test_bind_host_is_overridable_for_remote_setups(monkeypatch):
    """Running Live and the MCP server on separate machines stays possible,
    but only as a deliberate choice."""
    monkeypatch.setenv("ABLETON_MCP_HOST", "0.0.0.0")
    assert _load_script_module(fresh=True).HOST == "0.0.0.0"


def test_repo_and_bundled_copies_are_identical():
    """The installer ships the bundled copy; drift would install stale code."""
    assert REPO_SCRIPT.read_bytes() == BUNDLED_SCRIPT.read_bytes()


# ------------------------------------------------------------------ framing

class FakeClient(object):
    """Hands out prepared chunks, then EOF. Records what was written back."""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = []
        self.closed = False

    def settimeout(self, _timeout):
        pass

    def recv(self, _size):
        return self._chunks.pop(0) if self._chunks else b""

    def sendall(self, data):
        self.sent.append(data)

    def close(self):
        self.closed = True


def _serve(script_module, chunks):
    """Run one client session and decode whatever it wrote back."""
    inst = make_instance(script_module)
    inst.running = True
    received = []

    def _process(command):
        received.append(command)
        return {"status": "success", "result": {"echo": command.get("type")}}

    inst._process_command = _process
    client = FakeClient(chunks)
    inst._handle_client(client)
    replies = [json.loads(frame.decode("utf-8")) for frame in client.sent]
    return received, replies


def test_command_split_mid_utf8_character_still_parses(script_module):
    """Regression: decoding each chunk alone raised on the split, dropped the
    chunk, and wrote an error frame into a stream still awaiting the real
    response — leaving request and response permanently out of step.

    ensure_ascii=False is what puts raw multi-byte on the wire.
    """
    payload = json.dumps(
        {"type": "set_clip_name", "params": {"name": "café été"}},
        ensure_ascii=False,
    ).encode("utf-8")
    split = payload.index(b"\xc3") + 1  # between the two bytes of 'é'
    assert payload[:split].endswith(b"\xc3")

    received, replies = _serve(script_module, [payload[:split], payload[split:]])

    assert len(received) == 1
    assert received[0]["params"]["name"] == "café été"
    assert replies == [{"status": "success", "result": {"echo": "set_clip_name"}}]


def test_command_split_across_many_chunks_parses_once(script_module):
    payload = json.dumps({"type": "get_session_info", "params": {}}).encode("utf-8")
    chunks = [payload[i:i + 3] for i in range(0, len(payload), 3)]

    received, replies = _serve(script_module, chunks)

    assert len(received) == 1
    assert len(replies) == 1


def test_back_to_back_commands_each_get_one_reply(script_module):
    first = json.dumps({"type": "start_playback", "params": {}}).encode("utf-8")
    second = json.dumps({"type": "stop_playback", "params": {}}).encode("utf-8")

    received, replies = _serve(script_module, [first, second])

    assert [c["type"] for c in received] == ["start_playback", "stop_playback"]
    assert len(replies) == 2


def test_oversized_request_is_refused_instead_of_buffered_forever(
        script_module, monkeypatch):
    """A client that never completes a command must not grow the buffer."""
    monkeypatch.setattr(script_module, "MAX_REQUEST_BYTES", 1024)
    junk = b'{"type": "' + b"x" * 4096

    received, replies = _serve(script_module, [junk, junk])

    assert received == []
    assert len(replies) == 1
    assert replies[0]["status"] == "error"


# ------------------------------------------------------------- browser tree

class FakeBrowserItem(object):
    def __init__(self, name, children=(), loadable=False, uri=None):
        self.name = name
        self.children = list(children)
        self.is_device = False
        self.is_loadable = loadable
        self.uri = uri


class FakeBrowser(object):
    def __init__(self, instruments):
        self.instruments = instruments


class FakeApplication(object):
    def __init__(self, browser=None):
        self.browser = browser


def test_browser_tree_returns_children_and_a_truthful_folder_count(script_module):
    """Regression: process_item never recursed (children was always []) and
    total_folders was never returned, so the server's header always read
    'showing 0 folders'."""
    kits = FakeBrowserItem("Kits", [FakeBrowserItem("808 Core Kit", loadable=True)])
    browser = FakeBrowser(FakeBrowserItem("Instruments", [kits]))
    inst = make_instance(script_module, application=FakeApplication(browser=browser))

    tree = inst.get_browser_tree("instruments")

    assert tree["total_folders"] >= 2  # Instruments and Kits both have children
    category = tree["categories"][0]
    assert [child["name"] for child in category["children"]] == ["Kits"]
    # Depth is capped one level below the category, so Kits reports more.
    assert category["children"][0]["has_more"] is True


def test_browser_tree_caps_a_huge_folder_and_flags_it(script_module):
    many = [FakeBrowserItem("Preset %d" % i, loadable=True) for i in range(200)]
    browser = FakeBrowser(FakeBrowserItem("Instruments", many))
    inst = make_instance(script_module, application=FakeApplication(browser=browser))

    category = inst.get_browser_tree("instruments")["categories"][0]

    assert len(category["children"]) == 64
    assert category["has_more"] is True


def _layout_instance(script_module, tracks=(), returns=()):
    inst = make_instance(script_module)
    inst._song = types.SimpleNamespace(tracks=tracks, return_tracks=returns)
    inst.schedule_message = lambda delay, task: task()
    return inst


def test_routing_uses_live_choices_and_refuses_ambiguous_names(script_module):
    main = types.SimpleNamespace(display_name="Main")
    bus = types.SimpleNamespace(display_name="Bus")
    duplicate_bus = types.SimpleNamespace(display_name="Bus")
    blank = types.SimpleNamespace(display_name="")
    track = types.SimpleNamespace(
        name="Source", output_routing_type=main, output_routing_channel=blank,
        available_output_routing_types=[main, bus, duplicate_bus],
        available_output_routing_channels=[blank],
    )
    inst = _layout_instance(script_module, tracks=[track])
    target = {"track_index": 0, "direction": "output"}
    command = {"type": "set_track_routing", "params": dict(target, selector="type", choice="Bus")}

    rejected = inst._process_command(command)
    assert rejected["status"] == "error"
    assert track.output_routing_type is main

    track.available_output_routing_types.pop()
    changed = inst._process_command(command)
    assert changed["status"] == "success"
    assert track.output_routing_type is bus
    assert changed["result"]["type"] == "Bus"
    assert changed["result"]["channel"] is None
    assert changed["result"]["available_channels"] == []


def test_delete_return_checks_current_unique_name_and_index(script_module):
    returns = [types.SimpleNamespace(name="Reverb"), types.SimpleNamespace(name="Delay")]
    inst = _layout_instance(script_module, returns=returns)
    inst._song.delete_return_track = returns.pop

    def delete(index, expected_name):
        return inst._process_command({
            "type": "delete_return_track",
            "params": {"return_index": index, "expected_name": expected_name},
        })

    assert delete(-1, "Delay")["status"] == "error"
    assert delete(0, "Delay")["status"] == "error"
    returns[1].name = "Reverb"
    assert delete(0, "Reverb")["status"] == "error"
    assert len(returns) == 2
    returns[1].name = "Delay"
    assert delete(0, "Reverb")["result"]["deleted_name"] == "Reverb"
    assert [track.name for track in returns] == ["Delay"]


def test_create_return_removes_it_if_live_rejects_name(script_module):
    class UnnameableReturn:
        @property
        def name(self):
            return "New Return"

        @name.setter
        def name(self, _name):
            raise RuntimeError("Live rejected the name")

    original = types.SimpleNamespace(name="Reverb")
    returns = [original]
    inst = _layout_instance(script_module, returns=returns)
    inst._song.create_return_track = lambda: returns.append(UnnameableReturn())
    inst._song.delete_return_track = returns.pop

    rejected = inst._process_command({
        "type": "create_return_track", "params": {"name": "Delay"},
    })

    assert rejected == {"status": "error", "result": {}, "message": "Live rejected the name"}
    assert returns == [original]


def test_create_return_rejects_repeat_with_live_letter_prefix(script_module):
    returns = [types.SimpleNamespace(name="A-Practice FX")]
    inst = _layout_instance(script_module, returns=returns)
    inst._song.create_return_track = lambda: pytest.fail("Return creation must be refused")

    rejected = inst._process_command({
        "type": "create_return_track", "params": {"name": "Practice FX"},
    })

    assert rejected["status"] == "error"
    assert "already exists" in rejected["message"]


def test_set_send_maps_percent_to_live_range_and_refuses_invalid_values(script_module):
    send = types.SimpleNamespace(min=0.2, max=0.8, value=0.2, is_enabled=True)
    other_send = types.SimpleNamespace(value=0.1)
    track = types.SimpleNamespace(
        name="Source", mixer_device=types.SimpleNamespace(sends=[other_send, send]),
    )
    returns = [types.SimpleNamespace(name="Reverb"), types.SimpleNamespace(name="Delay")]
    inst = _layout_instance(script_module, tracks=[track], returns=returns)

    def set_send(percent, expected_return_name=None):
        return inst._process_command({
            "type": "set_track_send",
            "params": {
                "track_index": 0,
                "return_index": 1,
                "percent": percent,
                "expected_return_name": expected_return_name,
            },
        })

    assert set_send(float("nan"))["status"] == "error"
    assert set_send(101)["status"] == "error"
    assert set_send(50, "Reverb")["status"] == "error"
    assert send.value == 0.2

    changed = set_send(50, "Delay")
    assert changed["status"] == "success"
    assert changed["result"]["percent"] == pytest.approx(50)
    assert changed["result"]["return_name"] == "Delay"
    assert send.value == pytest.approx(0.5)
    assert other_send.value == 0.1

    send.is_enabled = False
    assert set_send(0)["status"] == "error"
    assert send.value == pytest.approx(0.5)
