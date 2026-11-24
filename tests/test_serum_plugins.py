from unittest.mock import Mock, call

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from MCP_Server import server


VST3_PATH = {"path": "plugins/VST3/Xfer Records"}
AU_PATH = {"path": "plugins/AUv2/Xfer Records"}


def plugin(name, uri, *, loadable=True):
    return {"name": name, "uri": uri, "is_loadable": loadable, "is_device": False}


@pytest.fixture
def ableton(monkeypatch):
    connection = Mock(spec=server.AbletonConnection)
    monkeypatch.setattr(server, "get_ableton_connection", lambda: connection)
    return connection


def test_list_serum_plugins_uses_exact_loadable_entries(ableton):
    ableton.send_command.side_effect = [
        {"items": [
            plugin("Serum 2", "vst3-instrument"),
            plugin("Serum 2 FX", "vst3-fx"),
            plugin("Serum 2 FX Preview", "preview"),
            plugin("Serum 2 FX", "folder", loadable=False),
            plugin("Serum 2", None),
        ]},
        {"items": [plugin("Serum 2 FX", "au-fx")]},
    ]

    assert server.list_serum_plugins() == {"plugins": [
        {"name": "Serum 2", "format": "vst3", "uri": "vst3-instrument"},
        {"name": "Serum 2 FX", "format": "vst3", "uri": "vst3-fx"},
        {"name": "Serum 2 FX", "format": "au", "uri": "au-fx"},
    ]}
    assert ableton.send_command.call_args_list == [
        call("get_browser_items_at_path", VST3_PATH),
        call("get_browser_items_at_path", AU_PATH),
    ]


def test_auto_prefers_vst3_for_the_requested_variant(ableton):
    ableton.send_command.side_effect = [
        {"items": [plugin("Serum 2", "instrument"), plugin("Serum 2 FX", "vst3-fx")]},
        {"loaded": True},
    ]

    assert server.load_serum_plugin(3, "fx") == {
        "name": "Serum 2 FX",
        "format": "vst3",
        "uri": "vst3-fx",
        "track_index": 3,
        "status": "load_requested",
    }
    assert ableton.send_command.call_args_list == [
        call("get_browser_items_at_path", VST3_PATH),
        call("load_browser_item", {"track_index": 3, "item_uri": "vst3-fx"}),
    ]


def test_auto_uses_au_when_vst3_folder_is_missing(ableton):
    ableton.send_command.side_effect = [
        {"error": "Path part 'VST3' not found", "items": []},
        {"items": [plugin("Serum 2", "au-instrument")]},
        {"is_midi_track": True},
        {"loaded": True},
    ]

    assert server.load_serum_plugin(0, "instrument")["format"] == "au"
    assert ableton.send_command.call_args_list == [
        call("get_browser_items_at_path", VST3_PATH),
        call("get_browser_items_at_path", AU_PATH),
        call("get_track_info", {"track_index": 0}),
        call("load_browser_item", {"track_index": 0, "item_uri": "au-instrument"}),
    ]


def test_explicit_format_does_not_fall_back(ableton):
    ableton.send_command.return_value = {"items": []}

    with pytest.raises(ToolError, match="unavailable .*'au'"):
        server.load_serum_plugin(0, "fx", "au")

    ableton.send_command.assert_called_once_with("get_browser_items_at_path", AU_PATH)


def test_discovery_failure_does_not_trigger_a_load_or_fallback(ableton):
    ableton.send_command.side_effect = ConnectionError("connection lost")

    with pytest.raises(ConnectionError, match="connection lost"):
        server.load_serum_plugin(0, "fx")

    ableton.send_command.assert_called_once_with("get_browser_items_at_path", VST3_PATH)


def test_ambiguous_entries_do_not_load(ableton):
    ableton.send_command.return_value = {
        "items": [plugin("Serum 2 FX", "first"), plugin("Serum 2 FX", "second")],
    }

    with pytest.raises(ToolError, match="multiple loadable"):
        server.load_serum_plugin(0, "fx")

    ableton.send_command.assert_called_once_with("get_browser_items_at_path", VST3_PATH)


def test_instrument_refuses_audio_track(ableton):
    ableton.send_command.side_effect = [
        {"items": [plugin("Serum 2", "au-instrument")]},
        {"is_midi_track": False},
    ]

    with pytest.raises(ToolError, match="requires a MIDI track"):
        server.load_serum_plugin(0, "instrument", "au")

    assert ableton.send_command.call_args_list == [
        call("get_browser_items_at_path", AU_PATH),
        call("get_track_info", {"track_index": 0}),
    ]


def test_interrupted_load_is_not_retried(ableton):
    ableton.send_command.side_effect = [
        {"items": [plugin("Serum 2 FX", "vst3-fx")]},
        ConnectionError("response missing"),
    ]

    with pytest.raises(ToolError, match="Check the device chain before retrying"):
        server.load_serum_plugin(0, "fx")

    assert ableton.send_command.call_args_list == [
        call("get_browser_items_at_path", VST3_PATH),
        call("load_browser_item", {"track_index": 0, "item_uri": "vst3-fx"}),
    ]
