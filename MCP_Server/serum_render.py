"""Render and compare Serum patches with repeatable MIDI or audio stimuli."""

from __future__ import annotations

import hashlib
import math
import os
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Annotated

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .serum_state import AuditionInput, RENDER_RATE, SerumError, SerumState, Variant, initial_state, run_host

MAX_SECONDS = 30
SPECTRAL_BANDS = ((20, 120), (120, 500), (500, 2000), (2000, 6000), (6000, 24000))


class AuditionNote(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    pitch: Annotated[int, Field(strict=True, ge=0, le=127)]
    start: float = Field(default=0, ge=0, lt=MAX_SECONDS)
    length: float = Field(default=1, ge=0.001, lt=MAX_SECONDS)
    velocity: float = Field(default=0.75, gt=0, le=1)
    channel: Annotated[int, Field(strict=True, ge=0, le=15)] = 0


class Audition(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    seconds: float | None = Field(default=None, ge=0.05, le=MAX_SECONDS)
    tempo: float = Field(default=120, ge=20, le=300)
    notes: list[AuditionNote] = Field(default_factory=list, max_length=256)
    audio_path: Annotated[str, Field(min_length=1, max_length=4096)] | None = None


def _wav_samples(source: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(source), "rb") as file:
            count, channels, rate, width = file.getnframes(), file.getnchannels(), file.getframerate(), file.getsampwidth()
            if channels not in (1, 2) or width not in (1, 2, 3, 4) or not 8000 <= rate <= 192000:
                raise SerumError("Use mono or stereo PCM WAV at 8–192 kHz.")
            if not 0 < count <= rate * MAX_SECONDS:
                raise SerumError(f"Use a nonempty WAV at most {MAX_SECONDS} seconds long.")
            raw = file.readframes(count)
    except (wave.Error, EOFError) as exc:
        raise SerumError("Cannot read the input PCM WAV.") from exc
    if len(raw) != count * channels * width:
        raise SerumError("Input WAV audio is incomplete.")
    if width == 1:
        values = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128) / 128
    elif width == 3:
        pieces = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        words = (pieces[:, 0] << 8) | (pieces[:, 1] << 16) | (pieces[:, 2] << 24)
        values = words.astype(np.float32) / 2147483648
    else:
        values = np.frombuffer(raw, dtype=f"<i{width}").astype(np.float32) / (1 << (8 * width - 1))
    samples = values.reshape(-1, channels)
    return (np.repeat(samples, 2, axis=1) if channels == 1 else samples), rate


def _stereo_input(path: str) -> np.ndarray:
    source = Path(path).expanduser()
    if not source.is_absolute() or source.suffix.casefold() != ".wav" or not source.is_file():
        raise SerumError("Pass an absolute local PCM WAV path for Serum 2 FX.")
    data, rate = _wav_samples(source)
    if rate == RENDER_RATE:
        return data
    with tempfile.TemporaryDirectory(prefix="ableton-mcp-serum-resample-") as temp:
        destination = Path(temp) / "resampled.wav"
        try:
            subprocess.run(["/usr/bin/afconvert", "-f", "WAVE", "-d", f"LEI24@{RENDER_RATE}", "-r", "127",
                            str(source), str(destination)], capture_output=True, check=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as exc:
            raise SerumError("Could not resample the FX input to 48 kHz.") from exc
        data, rate = _wav_samples(destination)
    if rate != RENDER_RATE:
        raise SerumError("Unexpected FX input sample rate after resampling.")
    return data


def _prepare(variant: Variant, spec: Audition) -> tuple[AuditionInput, int, int, int]:
    if spec.audio_path is not None and variant != "fx":
        raise SerumError("Audio input is available for the fx variant. Use MIDI notes for the instrument.")
    input_audio = _stereo_input(spec.audio_path) if spec.audio_path is not None else None
    notes = spec.notes
    if variant == "instrument" and not notes:
        notes = [AuditionNote(pitch=48, length=min(1.0, (spec.seconds or 2.0) / 2))]
    events = [(round(note.start * RENDER_RATE), round((note.start + note.length) * RENDER_RATE), note) for note in notes]
    stimulus_frames = 0
    if variant == "fx":
        stimulus_frames = len(input_audio) if input_audio is not None else round(0.8 * RENDER_RATE)
    first = 0 if variant == "fx" else min(start for start, _, _ in events)
    last = max(stimulus_frames, max((end for _, end, _ in events), default=0))
    frames = round(spec.seconds * RENDER_RATE) if spec.seconds is not None else min(
        MAX_SECONDS * RENDER_RATE, max(2 * RENDER_RATE, last + RENDER_RATE),
    )
    if any(end >= frames for _, end, _ in events):
        raise SerumError("Set the render duration beyond the last MIDI note-off.")
    if input_audio is not None and len(input_audio) > frames:
        raise SerumError("The FX input is longer than the requested render duration.")
    attack_end = min(last, RENDER_RATE // 2)
    if variant == "instrument":
        attack_end = min(
            first + 4 * RENDER_RATE, max(end for start, end, _ in events if start == first),
            min((start for start, _, _ in events if start > first), default=last),
        )
    manifest = [f"{frames} {spec.tempo} {len(events)}"]
    manifest.extend(f"{start} {end} {note.pitch} {note.velocity} {note.channel}" for start, end, note in events)
    stereo = input_audio.astype("<f4", copy=False).tobytes() if input_audio is not None else None
    return AuditionInput(frames, "\n".join(manifest) + "\n", stereo), first, last, attack_end


def _level_db(value: float) -> float:
    return round(20 * math.log10(max(float(value), 1e-6)), 3)


def measure_audio(samples: np.ndarray, first: int, last: int, attack_end: int) -> dict:
    power = np.square(samples, dtype=np.float64).mean(axis=1)
    peak = float(np.abs(samples).max())
    rms = math.sqrt(power.mean())
    tail = _level_db(math.sqrt(power[last:].mean())) if last < len(samples) else None
    width = 0.0
    bands = {f"{low}_{high}": 0.0 for low, high in SPECTRAL_BANDS}
    centroid, attack = None, None
    if peak > 1e-6:
        side = np.subtract(samples[:, 0], samples[:, 1], dtype=np.float64) * 0.5
        width = round(float(np.square(side).mean() / power.mean()), 6)
        active = samples[first : max(first + 1, min(last, len(samples)))]
        window = np.hanning(len(active))
        spectra = np.square(np.abs(np.fft.rfft(active * window[:, None], axis=0))).sum(axis=1)
        frequencies = np.fft.rfftfreq(len(active), 1 / RENDER_RATE)
        total = float(spectra.sum())
        if total > 0:
            centroid = round(float(spectra @ frequencies) / total, 3)
            bands = {f"{low}_{high}": round(float(spectra[(frequencies >= low) & (frequencies < high)].sum()) / total, 6)
                     for low, high in SPECTRAL_BANDS}
        step = RENDER_RATE // 200
        attack_power = power[first:attack_end]
        if len(attack_power) >= step:
            blocks = np.sqrt(attack_power[:len(attack_power) // step * step].reshape(-1, step).mean(axis=1))
            if blocks.max() > 1e-6:
                onset = int(np.argmax(blocks >= blocks.max() * 0.1))
                full = int(np.argmax(blocks >= blocks.max() * 0.9))
                attack = (full - onset) * 5
    return {
        "rms_dbfs": _level_db(rms), "peak_dbfs": _level_db(peak), "tail_rms_dbfs": tail,
        "attack_ms": attack, "spectral_centroid_hz": centroid, "band_energy_fraction": bands,
        "stereo_side_fraction": width, "clipped_frames": int(np.count_nonzero((np.abs(samples) >= 1).any(axis=1))),
        "silent": peak <= 1e-6,
    }


def _save_audio(samples: np.ndarray, digest: str) -> Path:
    default = Path.home() / "Library" / "Application Support" / "AbletonMCP" / "Serum" / "Auditions"
    folder = Path(os.environ.get("ABLETON_MCP_SERUM_AUDITIONS", default)).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / f"{digest}.wav"
    pcm = np.rint(np.clip(samples, -1, 1 - 2**-23) * 2**23).astype("<i4")
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        staged = Path(temp) / "audio.wav"
        with wave.open(str(staged), "wb") as file:
            file.setparams((2, 3, RENDER_RATE, len(samples), "NONE", "not compressed"))
            file.writeframes(pcm.view(np.uint8).reshape(-1, 4)[:, :3].tobytes())
        os.replace(staged, path)
    return path


def render_audio(variant: Variant, state_id: str | None, spec: Audition, reference_state_id: str | None = None) -> dict:
    audition, first, last, attack_end = _prepare(variant, spec)
    state = initial_state(variant, state_id) if state_id is not None else None
    reference_state = state if reference_state_id == state_id else (
        initial_state(variant, reference_state_id) if reference_state_id is not None else None
    )

    def render_one(patch: SerumState | None) -> tuple[np.ndarray, dict]:
        _, raw = run_host(variant, patch, render=True, audition=audition)
        samples = np.frombuffer(raw, dtype="<f4").reshape(-1, 2)
        digest = hashlib.sha256(raw).hexdigest()
        return samples, {"audio_sha256": digest, "metrics": measure_audio(samples, first, last, attack_end)}

    samples, primary = render_one(state)
    reference_samples, reference = (
        render_one(reference_state) if reference_state_id is not None and reference_state_id != state_id else (samples, primary)
    )
    path = _save_audio(samples, primary["audio_sha256"])
    result = {"variant": variant, "sample_rate": RENDER_RATE, "frames": audition.frames,
              "state_id": state_id, "path": str(path), **primary}
    if reference_state_id is not None:
        reference_path = path if reference["audio_sha256"] == primary["audio_sha256"] else _save_audio(
            reference_samples, reference["audio_sha256"],
        )
        result["reference"] = {"state_id": reference_state_id, "path": str(reference_path), **reference}
        metrics = result["metrics"]
        baseline = reference["metrics"]
        result["delta"] = {key: round(value - baseline[key], 6) for key, value in metrics.items()
                           if type(value) in (int, float) and type(baseline[key]) in (int, float)}
        result["delta"]["band_energy_fraction"] = {
            band: round(value - baseline["band_energy_fraction"][band], 6)
            for band, value in metrics["band_energy_fraction"].items()
        }
    return result
