import hashlib
import struct

import pytest

from MCP_Server import serum_controls, serum_state
from MCP_Server.serum_controls import SerumEdits
from MCP_Server.serum_state import SerumError, SerumState, decode_state, encode_state


def processor():
    metadata = {"product": "Serum2", "productVersion": "2.1.5", "component": "processor", "version": 11}
    return SerumState(metadata, {"Env0": {"plainParams": "default"}, "FXRack0": {"FX": []}, "Blob": b"embedded"})


def test_state_round_trip_preserves_native_binary_values():
    state = processor()
    raw = encode_state(state)
    readback = decode_state(raw)
    readback.require_processor("instrument")
    assert readback.data == state.data
    with pytest.raises(SerumError, match="Serum2FX"):
        readback.require_processor("fx")


def test_corrupted_or_oversized_frame_is_rejected():
    raw = encode_state(processor())
    bad_checksum = raw[:-1] + bytes([raw[-1] ^ 255])
    with pytest.raises(SerumError, match="checksum"):
        decode_state(bad_checksum)
    header_size, = struct.unpack_from("<Q", raw, len(serum_state.MAGIC))
    end = len(serum_state.MAGIC) + 8 + header_size
    wrong_size = raw[:end] + struct.pack("<I", 1) + raw[end + 4:]
    with pytest.raises(SerumError, match="length"):
        decode_state(wrong_size)


def test_file_id_detects_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_SERUM_STATES", str(tmp_path))
    raw = encode_state(processor())
    state_id = serum_state.save_state(raw)
    assert hashlib.sha256(raw).hexdigest() == state_id
    assert serum_state.read_saved_state(state_id, "instrument").data == processor().data
    (tmp_path / f"{state_id}.state").write_bytes(b"corrupt")
    with pytest.raises(SerumError, match="does not match"):
        serum_state.read_saved_state(state_id, "instrument")


def test_verified_edits_can_materialize_default_parameter_map(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_SERUM_STATES", str(tmp_path))
    monkeypatch.setattr(serum_controls, "initial_state", lambda *args: processor())
    monkeypatch.setattr(serum_controls, "run_host", lambda variant, state: (encode_state(state), None))
    edits = SerumEdits.model_validate({"values": [{"path": ["Env0", "plainParams", "kParamAttack"], "value": 0.2}]})

    result = serum_controls.edit_state("instrument", edits)

    assert result["changes"] == [{"path": ["Env0", "plainParams", "kParamAttack"], "value": 0.2}]
    assert serum_state.read_saved_state(result["state_id"], "instrument").data["Env0"]["plainParams"] == {
        "kParamAttack": 0.2
    }


def test_readback_failure_does_not_publish_a_state(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_SERUM_STATES", str(tmp_path))
    monkeypatch.setattr(serum_controls, "initial_state", lambda *args: processor())
    monkeypatch.setattr(serum_controls, "run_host", lambda *args: (encode_state(processor()), None))
    edits = SerumEdits.model_validate({"values": [{"path": ["Env0", "plainParams", "kParamAttack"], "value": 0.2}]})
    with pytest.raises(SerumError, match="did not retain"):
        serum_controls.edit_state("instrument", edits)
    assert list(tmp_path.iterdir()) == []


def test_asset_reference_cannot_escape_category(tmp_path, monkeypatch):
    monkeypatch.setattr(serum_controls, "ASSET_ROOT", tmp_path)
    (tmp_path / "Tables").mkdir()
    (tmp_path / "private.wav").touch()
    edits = SerumEdits.model_validate({"oscillator_assets": [
        {"oscillator": 1, "mode": "wavetable", "relative_path": "../private.wav"},
    ]})
    with pytest.raises(SerumError, match="relative"):
        serum_controls.edit_state("instrument", edits)


@pytest.mark.parametrize("asset", ["../private.wav", "/private/tmp/impulse.wav"])
def test_nested_convolver_reference_is_checked_before_native_render(asset, monkeypatch):
    monkeypatch.setattr(serum_controls, "initial_state", lambda *args: processor())
    monkeypatch.setattr(serum_controls, "run_host", lambda *args: pytest.fail("unexpected native render"))
    edits = SerumEdits.model_validate({"values": [
        {"path": ["FXRack0", "FX"], "value": [{"type": 11, "FXConv": {"relativePathToIR": asset}}]},
    ]})
    with pytest.raises(SerumError, match="relative"):
        serum_controls.edit_state("instrument", edits)


def test_fx_replacement_keeps_requested_order(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_SERUM_STATES", str(tmp_path))
    monkeypatch.setattr(serum_controls, "initial_state", lambda *args: processor())
    monkeypatch.setattr(serum_controls, "run_host", lambda variant, state: (encode_state(state), None))
    edits = SerumEdits.model_validate({"effect_chains": [
        {"bus": "main", "modules": [{"module": "equalizer"}, {"module": "distortion"}]},
    ]})
    result = serum_controls.edit_state("instrument", edits)
    value = result["changes"][0]["value"]
    assert [entry["type"] for entry in value] == [7, 0]
    assert "FXEQ" in value[0]
    assert "FXDistortion" in value[1]


def test_verified_native_defaults_allow_center_and_eq_shelf_reset(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_SERUM_STATES", str(tmp_path))
    base = processor()
    base.data["Oscillator1"] = {"plainParams": {"kParamPan": -10}}
    readback = processor()
    readback.data["Oscillator1"] = {"plainParams": {}}
    readback.data["FXRack0"]["FX"] = [{"type": 7, "FXEQ": {"plainParams": {}}}]
    monkeypatch.setattr(serum_controls, "initial_state", lambda *args: base)
    monkeypatch.setattr(serum_controls, "run_host", lambda *args: (encode_state(readback), None))
    pan = ["Oscillator1", "plainParams", "kParamPan"]
    edits = SerumEdits.model_validate({
        "values": [{"path": pan, "value": 0}],
        "effect_chains": [{"modules": [{"module": "equalizer", "parameters": {"kParamType2": 0}}]}],
    })
    checked = serum_controls.edit_state("instrument", edits)["changes"]
    assert checked[0] == {"path": pan, "value": 0, "native_defaults": [pan]}
    assert checked[1]["native_defaults"] == [["FXRack0", "FX", 0, "FXEQ", "plainParams", "kParamType2"]]
    assert len(list(tmp_path.iterdir())) == 1

    edits.values[0].value = 10
    with pytest.raises(SerumError, match="did not retain"):
        serum_controls.edit_state("instrument", edits)
    assert len(list(tmp_path.iterdir())) == 1
