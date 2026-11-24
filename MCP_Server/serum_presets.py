"""Import and export native Serum 2 presets through verified processor state."""

from __future__ import annotations

import os
import plistlib
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Annotated, Literal
from xml.parsers.expat import ExpatError

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .serum_controls import MISSING, equivalent
from .serum_state import (
    PRODUCTS, STATE_LIMIT, STATE_VERSION, SUPPORTED_BUILD, SerumError, SerumState, Variant,
    decode_state, encode_state, initial_state, read_limited, read_saved_state, run_host, save_state,
)

EXTENSION = ".SerumPreset"
FILE_ONLY = {"fileType", "presetName", "presetAuthor", "presetComment", "presetDescription", "tags"}
PROCESSOR_ONLY = {"component", "modMatrixLocked", "scalarCurvesLocked"}
VST3_CLASS_IDS = {"instrument": b"56534558667350736572756D20320000", "fx": b"56534558667351736572756D20322066"}
LIVE_PRESET_FOLDER = "Serum MCP Presets"
LIVE_PRESET_BROWSER = f"user_library/{LIVE_PRESET_FOLDER}"
LIVE_PRESET_EXTENSIONS = {"vst3": ".vstpreset", "au": ".aupreset"}
LiveFormat = Literal["vst3", "au"]


class PresetDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=96)]
    author: str = Field(default="", max_length=128)
    description: str = Field(default="", max_length=1024)
    comment: str = Field(default="", max_length=1024)
    tags: list[Annotated[str, StringConstraints(min_length=1, max_length=48)]] = Field(
        default_factory=list, max_length=20,
    )


def preset_directory() -> Path:
    default = Path.home() / "Library" / "Application Support" / "AbletonMCP" / "Serum" / "Presets"
    return Path(os.environ.get("ABLETON_MCP_SERUM_PRESETS", default)).expanduser().resolve()


def preset_filename(name: str, extension: str) -> str:
    if not name or name.startswith(".") or name.casefold().endswith(extension.casefold()) or any(
        char in "/\\:" or ord(char) < 32 or ord(char) == 127 for char in name
    ) or len(name.encode("utf-8")) + len(extension) > 255:
        raise SerumError("Use a preset name without path separators, control characters, or file extension.")
    return name + extension


def native_preset(state: SerumState, details: PresetDetails) -> SerumState:
    metadata = {key: value for key, value in state.metadata.items() if key not in PROCESSOR_ONLY | {"hash"}}
    metadata.update(
        fileType="SerumPreset", presetName=details.name, presetAuthor=details.author,
        presetDescription=details.description, presetComment=details.comment, tags=details.tags,
    )
    data = {key: value for key, value in state.data.items() if key not in PROCESSOR_ONLY | {"hash"}}
    return SerumState(metadata, data | metadata)


def _publish_preset(folder: Path, filename: str, raw: bytes) -> Path:
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = folder / filename
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        staged = Path(temp) / filename
        staged.write_bytes(raw)
        try:
            os.link(staged, destination)
        except FileExistsError as exc:
            raise SerumError(f"A Serum preset called {filename!r} already exists in the export folder.") from exc
    return destination


def export_preset(variant: Variant, state_id: str, details: PresetDetails) -> dict:
    filename = preset_filename(details.name, EXTENSION)
    state = read_saved_state(state_id, variant)
    raw = encode_state(native_preset(state, details))
    destination = _publish_preset(preset_directory(), filename, raw)
    return {
        "variant": variant, "state_id": state_id, "path": str(destination), "name": details.name,
        "product_version": SUPPORTED_BUILD, "schema_version": STATE_VERSION,
    }


def live_preset_directory() -> Path:
    default = Path.home() / "Music" / "Ableton" / "User Library"
    library = Path(os.environ.get("ABLETON_MCP_LIVE_USER_LIBRARY", default)).expanduser()
    if not library.is_absolute() or not library.is_dir():
        raise SerumError("Set ABLETON_MCP_LIVE_USER_LIBRARY to Live's existing, absolute User Library directory.")
    return library / LIVE_PRESET_FOLDER


def vst3_preset(variant: Variant, processor: bytes) -> bytes:
    header_size = 48
    header = struct.pack("<4si32sq", b"VST3", 1, VST3_CLASS_IDS[variant], header_size + len(processor))
    chunk_list = struct.pack("<4si4sqq", b"List", 1, b"Comp", header_size, len(processor))
    return header + processor + chunk_list


def _vst3_processor(raw: bytes, variant: Variant) -> bytes:
    header_size = 48
    if len(raw) < header_size or raw[:8] != b"VST3\x01\x00\x00\x00" or raw[8:40] != VST3_CLASS_IDS[variant]:
        raise SerumError(f"Expected a VST3 preset for {PRODUCTS[variant]}.")
    chunk_index, = struct.unpack_from("<q", raw, 40)
    if not header_size <= chunk_index <= len(raw) - 8:
        raise SerumError("Invalid VST3 chunk index.")
    list_id, count = struct.unpack_from("<4si", raw, chunk_index)
    if list_id != b"List" or not 1 <= count <= 5 or chunk_index + 8 + 20 * count != len(raw):
        raise SerumError("Invalid VST3 chunk table.")
    processor = None
    for index in range(count):
        tag, start, size = struct.unpack_from("<4sqq", raw, chunk_index + 8 + 20 * index)
        if size < 0 or not header_size <= start <= start + size <= chunk_index:
            raise SerumError("Invalid VST3 chunk range.")
        if tag == b"Comp":
            if processor is not None:
                raise SerumError("Multiple processor chunks in VST3 preset.")
            processor = raw[start : start + size]
    if not processor:
        raise SerumError("No processor state in VST3 preset.")
    return processor


def _read_au_preset(path: Path, variant: Variant) -> dict:
    try:
        preset = plistlib.loads(read_limited(path, 2 * STATE_LIMIT))
    except (plistlib.InvalidFileException, ExpatError, ValueError) as exc:
        raise SerumError("Invalid Audio Units preset plist.") from exc
    identity = ("aumu", "Xf2X") if variant == "instrument" else ("aumf", "Xf2Y")
    codes = dict(zip(("type", "subtype", "manufacturer"), (*identity, "XFER")))
    if not isinstance(preset, dict) or any(
        preset.get(key) != int.from_bytes(code.encode(), "big") for key, code in codes.items()
    ) or not isinstance(preset.get("Processor State"), bytes):
        raise SerumError(f"Expected an Audio Units preset for {PRODUCTS[variant]}.")
    return preset


def _au_preset(variant: Variant, state: SerumState, name: str) -> bytes:
    default = Path.home() / ".cache" / "ableton-mcp" / "serum-au-preset"
    helper = Path(os.environ.get("ABLETON_MCP_SERUM_AU_HOST", default)).expanduser()
    if not helper.is_absolute() or not helper.is_file() or not os.access(helper, os.X_OK):
        raise SerumError("Set ABLETON_MCP_SERUM_AU_HOST to the compiled Audio Units preset helper.")
    with tempfile.TemporaryDirectory(prefix="ableton-mcp-serum-au-") as temp:
        folder = Path(temp)
        source, destination = folder / "processor.state", folder / "export.aupreset"
        source.write_bytes(encode_state(state))
        try:
            result = subprocess.run([str(helper), variant, str(source), str(destination), name],
                                    cwd=folder, capture_output=True, text=True, errors="replace", timeout=20)
        except subprocess.TimeoutExpired as exc:
            raise SerumError("Serum Audio Units preset export timed out.") from exc
        if result.returncode:
            raise SerumError(f"Serum Audio Units export failed. {result.stderr.strip()[-300:]}")
        preset = _read_au_preset(destination, variant)
    readback = decode_state(preset["Processor State"])
    readback.require_processor(variant)
    if not equivalent(readback.data, state.data):
        raise SerumError("Serum Audio Units did not retain the requested processor state.")
    controller_raw = preset.get("Controller State")
    if not isinstance(controller_raw, bytes):
        raise SerumError("Missing Serum Audio Units controller state.")
    controller = decode_state(controller_raw)
    if controller.metadata.get("component") != "controller" or controller.metadata.get("product") != PRODUCTS[variant]:
        raise SerumError("Unexpected Serum Audio Units controller state.")
    controller.metadata["presetName"] = name
    controller.data["presetName"] = name
    preset["Controller State"] = encode_state(controller)
    return plistlib.dumps(preset)


def export_live_preset(variant: Variant, state_id: str, name: str, plugin_format: LiveFormat = "vst3") -> dict:
    filename = preset_filename(name, LIVE_PRESET_EXTENSIONS[plugin_format])
    state = read_saved_state(state_id, variant)
    raw = vst3_preset(variant, encode_state(state)) if plugin_format == "vst3" else _au_preset(variant, state, name)
    destination = _publish_preset(live_preset_directory(), filename, raw)
    return {
        "variant": variant, "state_id": state_id, "path": str(destination), "name": name, "filename": filename,
        "format": plugin_format, "browser_path": LIVE_PRESET_BROWSER,
    }


def check_live_preset(variant: Variant, name: str, plugin_format: LiveFormat) -> str:
    filename = preset_filename(name, LIVE_PRESET_EXTENSIONS[plugin_format])
    path = live_preset_directory() / filename
    if not path.is_file():
        raise SerumError(f"Export {filename!r} into the managed Live User Library folder first.")
    processor = (_vst3_processor(read_limited(path, STATE_LIMIT + 1024), variant) if plugin_format == "vst3"
                 else _read_au_preset(path, variant)["Processor State"])
    decode_state(processor).require_processor(variant)
    return filename


def import_preset(variant: Variant, source: str) -> dict:
    path = Path(source).expanduser()
    if not path.is_absolute() or path.suffix.casefold() != EXTENSION.casefold() or not path.is_file():
        raise SerumError("Pass the absolute path of an existing .SerumPreset file.")
    preset = decode_state(read_limited(path))
    header = preset.metadata
    if header.get("fileType") != "SerumPreset" or header.get("product") != PRODUCTS[variant] or "component" in header:
        raise SerumError(f"Expected a native {PRODUCTS[variant]} preset.")
    if header.get("version") != STATE_VERSION or header.get("productVersion") != SUPPORTED_BUILD:
        raise SerumError(f"Open and resave the preset in Serum {SUPPORTED_BUILD} to migrate it to schema {STATE_VERSION}.")
    base = initial_state(variant)
    content = {key: value for key, value in preset.data.items() if key not in FILE_ONLY | PROCESSOR_ONLY}
    requested = SerumState(base.metadata, base.data | content)
    raw, _ = run_host(variant, requested)
    normalized = decode_state(raw).data
    normalized_sections = sorted(
        key for key, value in content.items() if not equivalent(normalized.get(key, MISSING), value)
    )
    return {
        "variant": variant, "state_id": save_state(raw), "path": str(path.resolve()),
        "details": {key: header[key] for key in sorted(FILE_ONLY - {"fileType"}) if key in header},
        "normalized_sections": normalized_sections,
    }
