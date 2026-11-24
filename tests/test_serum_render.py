import wave

import numpy as np
import pytest

from MCP_Server.serum_render import Audition, AuditionNote, _prepare, _wav_samples, measure_audio
from MCP_Server.serum_state import RENDER_RATE, SerumError


def test_audition_keeps_note_offsets_and_partial_last_block():
    spec = Audition(seconds=1.013, tempo=150, notes=[
        AuditionNote(pitch=48, start=0.017, length=0.291, velocity=0.5),
        AuditionNote(pitch=55, start=0.206, length=0.115),
    ])
    packed, first, last, attack_end = _prepare("instrument", spec)
    assert packed.events.splitlines() == ["48624 150.0 2", "816 14784 48 0.5 0", "9888 15408 55 0.75 0"]
    assert (first, last, attack_end) == (816, 15408, 9888)
    with pytest.raises(SerumError, match="last MIDI note-off"):
        _prepare("instrument", spec.model_copy(update={"seconds": 0.2}))


def test_pcm24_expands_mono_and_retains_negative_samples(tmp_path):
    source = tmp_path / "probe.wav"
    with wave.open(str(source), "wb") as file:
        file.setparams((1, 3, RENDER_RATE, 3, "NONE", "not compressed"))
        file.writeframes(bytes.fromhex("0000800000400000c0"))
    samples, rate = _wav_samples(source)
    assert rate == RENDER_RATE
    assert samples.tolist() == [[-1, -1], [0.5, 0.5], [-0.5, -0.5]]


def test_audio_analysis_distinguishes_spectral_bands_and_stereo():
    seconds = np.arange(RENDER_RATE) / RENDER_RATE
    low = 0.125 * np.sin(2 * np.pi * 82 * seconds)
    high = 0.125 * np.sin(2 * np.pi * 4000 * seconds)
    mono = measure_audio(np.column_stack([low, low]), 0, RENDER_RATE, RENDER_RATE // 2)
    wide = measure_audio(np.column_stack([high, -high]), 0, RENDER_RATE, RENDER_RATE // 2)
    assert mono["stereo_side_fraction"] == 0 and wide["stereo_side_fraction"] == 1
    assert mono["band_energy_fraction"]["20_120"] > 0.999
    assert wide["band_energy_fraction"]["2000_6000"] > 0.999
    assert mono["spectral_centroid_hz"] == 82 and wide["spectral_centroid_hz"] == 4000
    assert mono["clipped_frames"] == wide["clipped_frames"] == 0
