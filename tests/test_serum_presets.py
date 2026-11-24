from pathlib import Path
import wave

import pytest

from MCP_Server import serum_controls, serum_presets, serum_state
from MCP_Server.serum_presets import PresetDetails
from MCP_Server.serum_state import SerumError, SerumState


def test_native_export_import_preserves_state_and_refuses_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_SERUM_STATES", str(tmp_path / "states"))
    monkeypatch.setenv("ABLETON_MCP_SERUM_PRESETS", str(tmp_path / "presets"))
    metadata = {"component": "processor", "product": "Serum2", "productVersion": "2.1.5", "version": 11}
    data = metadata | {"modMatrixLocked": False, "WTOsc0": {"embeddedWTData": b"embedded"}}
    state = SerumState(metadata, data)
    raw = serum_state.encode_state(state)
    state_id = serum_state.save_state(raw)
    monkeypatch.setattr(serum_presets, "initial_state", lambda variant: SerumState(metadata, metadata))
    monkeypatch.setattr(serum_presets, "run_host", lambda variant, requested: (serum_state.encode_state(requested), None))

    details = PresetDetails(name="테스트", author="Author", tags=["Bass"])
    receipt = serum_presets.export_preset("instrument", state_id, details)
    destination = Path(receipt["path"])
    exported = destination.read_bytes()
    preset = serum_state.decode_state(exported)
    assert preset.metadata["fileType"] == preset.data["fileType"] == "SerumPreset"
    assert preset.metadata["presetName"] == preset.data["presetName"] == details.name
    assert not {"component", "modMatrixLocked", "hash"}.intersection(preset.data)
    assert preset.data["WTOsc0"]["embeddedWTData"] == b"embedded"

    imported = serum_presets.import_preset("instrument", receipt["path"])
    assert imported["details"]["presetAuthor"] == "Author"
    assert imported["normalized_sections"] == []
    imported_state = serum_state.read_saved_state(imported["state_id"], "instrument")
    assert imported_state.data == {key: value for key, value in data.items() if key != "modMatrixLocked"}

    with pytest.raises(SerumError, match="already exists"):
        serum_presets.export_preset("instrument", state_id, PresetDetails(name="테스트", author="Other"))
    assert destination.read_bytes() == exported
    with pytest.raises(SerumError, match="native Serum2FX"):
        serum_presets.import_preset("fx", receipt["path"])


@pytest.mark.parametrize("name", ["../escape", "wrong.SerumPreset", "/absolute", "a:b", "a\nb"])
def test_preset_export_rejects_names_that_change_destination(name):
    with pytest.raises(SerumError, match="preset name"):
        serum_presets.export_preset("instrument", "unused", PresetDetails(name=name))


def test_live_wrapper_validates_processor_and_chunk_table(tmp_path, monkeypatch):
    monkeypatch.setenv("ABLETON_MCP_LIVE_USER_LIBRARY", str(tmp_path))
    monkeypatch.setenv("ABLETON_MCP_SERUM_STATES", str(tmp_path / "states"))
    state = SerumState({"component": "processor", "product": "Serum2", "productVersion": "2.1.5", "version": 11}, {})
    state_id = serum_state.save_state(serum_state.encode_state(state))
    exported = serum_presets.export_live_preset("instrument", state_id, "Test instrument")
    expected = "Test instrument.vstpreset"
    assert serum_presets.check_live_preset("instrument", "Test instrument", "vst3") == expected
    with pytest.raises(SerumError, match="Serum2FX"):
        serum_presets.check_live_preset("fx", "Test instrument", "vst3")
    path = Path(exported["path"])
    path.write_bytes(path.read_bytes()[:-1])
    with pytest.raises(SerumError, match="chunk table"):
        serum_presets.check_live_preset("instrument", "Test instrument", "vst3")


def test_embedded_stereo_pcm_is_channel_major(tmp_path):
    source = tmp_path / "Stereo.wav"
    with wave.open(str(source), "wb") as file:
        file.setparams((2, 2, 44100, 2, "NONE", "not compressed"))
        file.writeframes(bytes.fromhex("00800000004000c0"))
    asset = serum_controls.EmbeddedOscillatorAsset(oscillator=2, mode="sample", wav_path=str(source), base_note=48)
    changes = dict((tuple(path), value) for path, value in serum_controls._embedded_asset_edits(asset))
    assert changes[("Oscillator1", "SampleOsc1", "embedded_audio")] == {
        "data": [[-1.0, 0.5], [0.0, -0.5]], "sampleRate": 44100, "table_name": "Stereo",
    }
    asset.mode = "wavetable"
    with pytest.raises(SerumError, match="2048-sample cycles"):
        serum_controls._embedded_asset_edits(asset)
