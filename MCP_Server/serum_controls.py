"""Checked edits for locally hosted Serum 2 processor state."""

from __future__ import annotations

import math
import re
import subprocess
import wave
from array import array
from copy import deepcopy
from pathlib import Path
from sys import byteorder
from typing import Annotated, Literal
from xml.etree import ElementTree

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictFloat, StrictInt, StrictStr

from .serum_state import (
    ASSET_ROOT, SUPPORTED_BUILD, SerumError, SerumState, Variant, decode_state,
    initial_state, run_host, save_state,
)

Mode = Literal["wavetable", "sample", "granular", "spectral", "multisample"]
EmbeddedMode = Literal["wavetable", "sample", "granular", "spectral"]
PathPart = StrictStr | StrictInt
StatePath = Annotated[list[PathPart], Field(min_length=1, max_length=12)]
ASSET_MODES: dict[Mode, tuple[str, str, str, str]] = {
    "wavetable": ("Tables", "WTOsc", "relativePathToWT", "kOsc_WT"),
    "sample": ("Samples", "SampleOsc", "samplePathRelative", "kOsc_Sample"),
    "granular": ("Samples", "GranularOsc", "samplePathRelative", "kOsc_Granular"),
    "spectral": ("Samples", "SpectralOsc", "samplePathRelative", "kOsc_Spectral"),
    "multisample": ("Multisamples", "MultiSampleOsc", "sfzPathRelative", "kOsc_MultiSample"),
}
FX_MODULES = {
    "distortion": (0, "FXDistortion"),
    "flanger": (1, "FXFlanger"),
    "phaser": (2, "FXPhaser"),
    "chorus": (3, "FXChorus"),
    "delay": (4, "FXDelay"),
    "compressor": (5, "FXComp"),
    "reverb": (6, "FXReverb"),
    "equalizer": (7, "FXEQ"),
    "filter": (8, "FXFilter"),
    "hyper_dimension": (9, "FXHyperD"),
    "bode": (10, "FXBode"),
    "convolver": (11, "FXConv"),
    "utility": (12, "FXUtils"),
    "split": (13, "FXSplit"),
    "split_three_band": (14, "FXSplit3"),
    "split_mid_side": (15, "FXSplitMS"),
}
RACKS = {"main": "FXRack0", "bus_1": "FXRack1", "bus_2": "FXRack2"}
ASSET_PATH_KEYS = {
    "relativePathToWT": "Tables",
    "samplePathRelative": "Samples",
    "sfzPathRelative": "Multisamples",
    "relativePathToIR": "Impulses",
    "relativePathToNoiseSample": "Samples/Factory Non-Tonal/Noises",
}
MEDIA_KEYS = {"embeddedWTData", "embedded_audio", "relativePathToWT", "pathToWT", "samplePathRelative", "samplePath"}
MAX_EMBEDDED_SAMPLES = 512 * 1024
OSCILLATORS = {"Oscillator0", "Oscillator1", "Oscillator2"}
MACROS = {f"Macro{index}" for index in range(8)}
MOD_SLOT = re.compile(r"ModSlot(?:[0-9]|[1-5][0-9]|6[0-3])\Z")
PARAM_NAME = re.compile(r"kParam[A-Za-z0-9_]{1,64}\Z")
MISSING = object()


class StateEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: StatePath
    value: JsonValue


class OscillatorAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oscillator: Annotated[int, Field(strict=True, ge=1, le=3)]
    mode: Mode
    relative_path: Annotated[str, Field(min_length=1, max_length=512)]
    base_note: Annotated[int, Field(strict=True, ge=0, le=127)] | None = None


class EmbeddedOscillatorAsset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    oscillator: Annotated[int, Field(strict=True, ge=1, le=3)]
    mode: EmbeddedMode
    wav_path: Annotated[str, Field(min_length=1, max_length=4096)]
    base_note: Annotated[int, Field(strict=True, ge=0, le=127)] = 60


class EffectModule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    module: str
    parameters: dict[str, StrictInt | StrictFloat | StrictStr] = Field(default_factory=dict, max_length=32)


class EffectChain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bus: Literal["main", "bus_1", "bus_2"] = "main"
    modules: list[EffectModule] = Field(max_length=16)


class SerumEdits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    values: list[StateEdit] = Field(default_factory=list, max_length=96)
    oscillator_assets: list[OscillatorAsset] = Field(default_factory=list, max_length=3)
    embedded_assets: list[EmbeddedOscillatorAsset] = Field(default_factory=list, max_length=3)
    effect_chains: list[EffectChain] = Field(default_factory=list, max_length=3)


def value_at(data: JsonValue, path: list[PathPart]):
    value = data
    for segment in path:
        if isinstance(segment, str) and isinstance(value, dict):
            value = value.get(segment, MISSING)
        elif type(segment) is int and isinstance(value, list) and 0 <= segment < len(value):
            value = value[segment]
        else:
            return MISSING
    return value


def native_default(data: dict, path: list[PathPart]):
    """Return a schema-11 default that the processor omits in a verified location."""
    if len(path) < 3 or path[-2] != "plainParams" or not isinstance(value_at(data, path[:-2]), dict):
        return MISSING
    root, field = path[0], path[-1]
    if len(path) == 3:
        if root in OSCILLATORS and field == "kParamType":
            return "kOsc_WT"
        if root in OSCILLATORS and field == "kParamPan":
            return 0
        if root in {"Oscillator1", "Oscillator2"} and field == "kParamEnable":
            return 0
        if (root == "Global0" and field == "kParamModWheel") or (root in MACROS and field == "kParamValue"):
            return 0
    if (len(path) == 6 and root in RACKS.values() and path[1] == "FX" and path[3] == "FXEQ"
            and value_at(data, [*path[:3], "type"]) == 7 and field in {"kParamType1", "kParamType2"}):
        return 0
    return MISSING


def equivalent(actual, expected) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return type(actual) is type(expected) and actual == expected
    if type(expected) in (int, float):
        return type(actual) in (int, float) and math.isclose(actual, expected, rel_tol=1e-6, abs_tol=1e-8)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and equivalent(actual[key], item) for key, item in expected.items())
    if isinstance(expected, list):
        return isinstance(actual, list) and len(actual) == len(expected) and all(
            equivalent(a, b) for a, b in zip(actual, expected)
        )
    return actual == expected


def describe(value, field=None):
    if value is MISSING:
        return "<omitted>"
    if isinstance(value, bytes):
        return {"embedded_bytes": len(value)}
    if field == "embeddedWTData" and isinstance(value, list):
        return {"embedded_frames": len(value)}
    if field == "embedded_audio" and isinstance(value, dict):
        channels = value.get("data", [])
        if not isinstance(channels, list):
            return {"embedded_audio": "unrecognized channel data"}
        frames = len(channels[0]) if channels and isinstance(channels[0], list) else 0
        return {"embedded_frames": frames, "channels": len(channels),
                "sample_rate": value.get("sampleRate"), "name": value.get("table_name")}
    if isinstance(value, dict):
        return {str(key): describe(item, key) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [describe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    return value if value is None or isinstance(value, (str, int, float, bool)) else type(value).__name__


def _asset_path(folder: str, relative: str) -> Path:
    base = (ASSET_ROOT / folder).resolve()
    path = Path(relative)
    if path.is_absolute() or ".." in path.parts or path.as_posix() == ".":
        raise SerumError("Use an installed asset path relative to its Serum category.")
    resolved = (base / path).resolve()
    if not resolved.is_relative_to(base) or not resolved.is_file():
        raise SerumError(f"Serum asset not found under {folder}: {relative}")
    return resolved


def list_assets(mode: Mode, search: str, offset: int, limit: int) -> dict:
    folder = ASSET_MODES[mode][0]
    extensions = {
        "Tables": {".wav"},
        "Samples": {".wav", ".aif", ".aiff", ".flac"},
        "Multisamples": {".sfz"},
    }[folder]
    base = (ASSET_ROOT / folder).resolve()
    if not base.is_dir():
        raise SerumError(f"Serum asset directory not found: {folder}")
    matches = sorted(
        path.relative_to(base).as_posix()
        for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in extensions
        and search.casefold() in path.relative_to(base).as_posix().casefold()
        and path.resolve().is_relative_to(base)
    )
    return {"mode": mode, "total": len(matches), "paths": matches[offset : offset + limit]}


def _audio_dimensions(path: Path) -> dict[str, int]:
    try:
        result = subprocess.run(
            ["/usr/bin/afinfo", "-x", str(path)], capture_output=True, check=True, timeout=10
        )
        track = ElementTree.fromstring(result.stdout).find(".//{*}track")
        if track is None:
            raise ValueError("missing audio track")
        rate = int(track.findtext("{*}sample_rate", "0"))
        channels = int(track.findtext("{*}num_channels", "0"))
        frames = track.findtext(".//{*}valid_frames")
        if frames is None:
            per_packet = int(track.findtext("{*}frames_per_packet", "1"))
            frames = int(track.findtext("{*}audio_packets", "0")) * per_packet
        frames = int(frames)
        if min(rate, channels, frames) <= 0 or channels > 2:
            raise ValueError("expected mono or stereo audio")
    except (OSError, subprocess.SubprocessError, ElementTree.ParseError, ValueError) as exc:
        raise SerumError(f"Cannot read Serum audio asset dimensions: {path.name}") from exc
    return {"sampleRate": rate, "numChannels": channels, "numFrames": frames}


def _validate_value(value, field: str | None = None, depth: int = 0) -> None:
    if depth > 20:
        raise SerumError("Serum edit exceeds the maximum nesting depth.")
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_value(item, key, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _validate_value(item, depth=depth + 1)
    elif type(value) is float and not math.isfinite(value):
        raise SerumError("Serum edits require finite numbers.")
    elif field in {"samplePath", "pathToWT"} and value:
        raise SerumError("Use the installed or embedded oscillator asset selector.")
    elif field in ASSET_PATH_KEYS and isinstance(value, str) and value:
        _asset_path(ASSET_PATH_KEYS[field], value)


def _set_value(data: dict, path: list[PathPart], value, reserved: set[str]) -> None:
    root = path[0]
    if not isinstance(root, str) or root in reserved or (root not in data and not MOD_SLOT.fullmatch(root)):
        raise SerumError(f"Unknown or reserved Serum section: {root}")
    _validate_value(value, path[-1] if isinstance(path[-1], str) else None)
    parent = value_at(data, path[:-1])
    leaf = path[-1]
    if path[-2:-1] == ["plainParams"] and (parent is MISSING or parent == "default"):
        module = value_at(data, path[:-2])
        if isinstance(module, dict):
            parent = module["plainParams"] = {}
    if isinstance(parent, dict) and isinstance(leaf, str) and leaf:
        parent[leaf] = value
    elif isinstance(parent, list) and type(leaf) is int and 0 <= leaf < len(parent):
        parent[leaf] = value
    else:
        raise SerumError(f"Serum edit has no matching parent: {path}")


def _asset_edits(asset: OscillatorAsset) -> list[tuple[list[PathPart], JsonValue]]:
    folder, prefix, name, engine = ASSET_MODES[asset.mode]
    source = _asset_path(folder, asset.relative_path)
    index = asset.oscillator - 1
    section = f"Oscillator{index}"
    target = [section, f"{prefix}{index}"]
    edits = [([section, "plainParams", "kParamType"], engine), ([*target, name], asset.relative_path)]
    if asset.mode != "multisample":
        edits.extend(([*target, key], value) for key, value in _audio_dimensions(source).items())
        if asset.base_note is not None:
            edits.append(([*target, "baseNote"], asset.base_note))
    elif source.suffix.lower() != ".sfz":
        raise SerumError("Serum multisample mode uses a local SFZ asset.")
    return edits


def _embedded_asset_edits(asset: EmbeddedOscillatorAsset) -> list[tuple[list[PathPart], JsonValue]]:
    source = Path(asset.wav_path).expanduser()
    if not source.is_absolute() or source.suffix.casefold() != ".wav" or not source.is_file():
        raise SerumError("Use the absolute path of a local PCM16 WAV file to embed.")
    try:
        with wave.open(str(source), "rb") as file:
            frames, channels, rate = file.getnframes(), file.getnchannels(), file.getframerate()
            if channels not in (1, 2) or file.getsampwidth() != 2 or not 8000 <= rate <= 192000:
                raise SerumError("Embedded assets require mono or stereo PCM16 WAV at 8–192 kHz.")
            if frames < 1 or frames * channels > MAX_EMBEDDED_SAMPLES:
                raise SerumError(f"Embedded assets are limited to {MAX_EMBEDDED_SAMPLES} PCM samples.")
            if asset.mode == "wavetable" and (channels != 1 or frames % 2048):
                raise SerumError("An embedded wavetable needs mono WAV data in 2048-sample cycles.")
            raw = file.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise SerumError("Cannot read the embedded WAV file.") from exc
    if len(raw) != frames * channels * 2:
        raise SerumError("Embedded WAV data is incomplete.")
    samples = array("h", raw)
    if byteorder == "big":
        samples.byteswap()
    audio = [[sample / 32768.0 for sample in samples[index::channels]] for index in range(channels)]
    _, prefix, _, engine = ASSET_MODES[asset.mode]
    index = asset.oscillator - 1
    section = f"Oscillator{index}"
    target = [section, f"{prefix}{index}"]
    media = {"numFrames": frames, "numChannels": channels, "sampleRate": rate}
    if asset.mode == "wavetable":
        media.update(embeddedWTData=audio[0], tableDisplayName=source.stem, interpolateAfterLoad=0)
    else:
        media.update(embedded_audio={"data": audio, "sampleRate": rate, "table_name": source.stem},
                     baseNote=asset.base_note)
    return [([section, "plainParams", "kParamType"], engine)] + [
        ([*target, key], value) for key, value in media.items()
    ]


def _effect_edits(chain: EffectChain) -> tuple[list[PathPart], JsonValue]:
    modules = []
    for item in chain.modules:
        if item.module not in FX_MODULES:
            raise SerumError(f"Unknown Serum effect: {item.module}")
        if any(not PARAM_NAME.fullmatch(name) for name in item.parameters):
            raise SerumError("Effect parameter names must match Serum's kParam names.")
        kind, key = FX_MODULES[item.module]
        modules.append({"type": kind, key: {"plainParams": item.parameters}})
    return [RACKS[chain.bus], "FX"], modules


def _with_native_defaults(actual, expected, data: dict, path: list[PathPart], defaults: list[list[PathPart]]):
    if actual is MISSING:
        value = native_default(data, path)
        if value is not MISSING:
            defaults.append(path)
        return value
    if isinstance(actual, dict) and isinstance(expected, dict):
        result = actual.copy()
        for key, child in expected.items():
            value = _with_native_defaults(actual.get(key, MISSING), child, data, [*path, key], defaults)
            if value is not MISSING:
                result[key] = value
        return result
    if isinstance(actual, list) and isinstance(expected, list) and len(actual) == len(expected):
        return [_with_native_defaults(got, wanted, data, [*path, index], defaults)
                for index, (got, wanted) in enumerate(zip(actual, expected))]
    return actual


def edit_state(variant: Variant, edits: SerumEdits, base_state_id: str | None = None) -> dict:
    changes = [(item.path, item.value) for item in edits.values]
    for asset in edits.oscillator_assets:
        changes.extend(_asset_edits(asset))
    for asset in edits.embedded_assets:
        changes.extend(_embedded_asset_edits(asset))
    changes.extend(_effect_edits(chain) for chain in edits.effect_chains)
    if not changes:
        raise SerumError("At least one Serum edit is required.")
    for i, (path, _) in enumerate(changes):
        if any(path[:len(other)] == other or other[:len(path)] == path for other, _ in changes[:i]):
            raise SerumError(f"Overlapping Serum edits are ambiguous: {path}")
    base = initial_state(variant, base_state_id)
    edited = deepcopy(base.data)
    for asset in [*edits.oscillator_assets, *edits.embedded_assets]:
        index = asset.oscillator - 1
        prefix = ASSET_MODES[asset.mode][1]
        target = value_at(edited, [f"Oscillator{index}", f"{prefix}{index}"])
        if isinstance(target, dict):
            for key in MEDIA_KEYS:
                target.pop(key, None)
    reserved = set(base.metadata) | {"component", "fileType", "hash", "product", "productVersion", "version"}
    for path, value in changes:
        _set_value(edited, path, value, reserved)
    result, _ = run_host(variant, SerumState(base.metadata, edited))
    actual = decode_state(result).data
    checked = []
    for path, expected in changes:
        readback = value_at(actual, path)
        defaults = []
        if not equivalent(readback, expected):
            readback = _with_native_defaults(readback, expected, actual, path, defaults)
            if not equivalent(readback, expected):
                raise SerumError(f"Serum did not retain {path}. Readback was {describe(readback, path[-1])!r}.")
        receipt = {"path": path, "value": describe(readback, path[-1])}
        if defaults:
            receipt["native_defaults"] = defaults
        checked.append(receipt)
    return {"state_id": save_state(result), "variant": variant, "build": SUPPORTED_BUILD, "changes": checked}
