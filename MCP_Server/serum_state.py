"""Read and apply versioned Serum 2 state through a separate VST3 host."""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import plistlib
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import cbor2
import zstandard as zstd

Variant = Literal["instrument", "fx"]
PRODUCTS: dict[Variant, str] = {"instrument": "Serum2", "fx": "Serum2FX"}
SUPPORTED_BUILD = "2.1.5"
STATE_VERSION = 11
STATE_LIMIT = 16 * 1024 * 1024
META_LIMIT = 64 * 1024
MAGIC = b"XferJson\0"
BUNDLE = Path("/Library/Audio/Plug-Ins/VST3/Serum2.vst3")
ASSET_ROOT = Path("/Library/Audio/Presets/Xfer Records/Serum 2 Presets")
STATE_ID = re.compile(r"[0-9a-f]{64}\Z")
RENDER_RATE = 48000
DEFAULT_RENDER_FRAMES = RENDER_RATE * 2


class SerumError(ValueError):
    """A state edit could not be validated or applied."""


@dataclass
class SerumState:
    metadata: dict[str, Any]
    data: dict[str, Any]

    def require_processor(self, variant: Variant) -> None:
        expected = {
            "component": "processor",
            "product": PRODUCTS[variant],
            "productVersion": SUPPORTED_BUILD,
            "version": STATE_VERSION,
        }
        if any(self.metadata.get(key) != value for key, value in expected.items()):
            raise SerumError(f"Expected {PRODUCTS[variant]} {SUPPORTED_BUILD} processor state (schema {STATE_VERSION}).")


@dataclass(frozen=True)
class AuditionInput:
    frames: int
    events: str
    audio: bytes | None = None


def read_limited(path: Path, limit: int = STATE_LIMIT) -> bytes:
    with path.open("rb") as file:
        raw = file.read(limit + 1)
    if len(raw) > limit:
        raise SerumError(f"{path.name} exceeds the {limit}-byte limit.")
    return raw


def decode_state(raw: bytes) -> SerumState:
    header_offset = len(MAGIC) + 8
    if len(raw) < header_offset + 8 or len(raw) > STATE_LIMIT or not raw.startswith(MAGIC):
        raise SerumError("Invalid Serum state header or file length.")
    header_size, = struct.unpack_from("<Q", raw, len(MAGIC))
    end = header_offset + header_size
    if not 0 < header_size <= META_LIMIT or end + 8 >= len(raw):
        raise SerumError("Invalid Serum metadata length.")
    size, codec = struct.unpack_from("<II", raw, end)
    if codec != 2 or not 0 < size <= STATE_LIMIT:
        raise SerumError("Expected a bounded Zstandard/CBOR Serum state.")
    frame = raw[end + 8:]
    try:
        metadata = json.loads(raw[header_offset:end])
        if not isinstance(metadata, dict) or metadata.get("hash") != hashlib.md5(frame, usedforsecurity=False).hexdigest():
            raise SerumError("Serum compressed state checksum does not match.")
        frame_size = zstd.frame_content_size(frame)
        if frame_size not in (size, zstd.CONTENTSIZE_UNKNOWN):
            raise SerumError("Serum compressed state length does not match.")
        content = zstd.ZstdDecompressor(max_window_size=STATE_LIMIT // 1024).decompress(
            frame, max_output_size=size, allow_extra_data=False
        )
        if len(content) != size:
            raise SerumError("Serum decoded state length does not match.")
        stream = io.BytesIO(content)
        data = cbor2.CBORDecoder(stream).decode()
        if stream.tell() != len(content) or not isinstance(data, dict) or any(not isinstance(key, str) for key in data):
            raise SerumError("Expected one Serum state map with text keys.")
    except (UnicodeError, json.JSONDecodeError, cbor2.CBORDecodeError, zstd.ZstdError, RecursionError) as exc:
        raise SerumError("Unreadable Serum state.") from exc
    return SerumState(metadata, data)


def encode_state(state: SerumState) -> bytes:
    content = cbor2.dumps(state.data)
    if len(content) > STATE_LIMIT:
        raise SerumError("Edited Serum state is too large.")
    frame = zstd.ZstdCompressor(level=3).compress(content)
    metadata = {**state.metadata, "hash": hashlib.md5(frame, usedforsecurity=False).hexdigest()}
    header = json.dumps(metadata, separators=(",", ":"), allow_nan=False).encode()
    raw = MAGIC + struct.pack("<Q", len(header)) + header + struct.pack("<II", len(content), 2) + frame
    if len(header) > META_LIMIT or len(raw) > STATE_LIMIT:
        raise SerumError("Encoded Serum state is too large.")
    return raw


def state_directory() -> Path:
    default = Path.home() / "Library" / "Application Support" / "AbletonMCP" / "Serum" / "States"
    return Path(os.environ.get("ABLETON_MCP_SERUM_STATES", default)).expanduser()


def read_saved_state(state_id: str, variant: Variant) -> SerumState:
    if not STATE_ID.fullmatch(state_id):
        raise SerumError("Invalid Serum state ID.")
    try:
        raw = read_limited(state_directory() / f"{state_id}.state")
    except FileNotFoundError as exc:
        raise SerumError("Serum state ID not found.") from exc
    if hashlib.sha256(raw).hexdigest() != state_id:
        raise SerumError("Serum state ID does not match the saved state.")
    state = decode_state(raw)
    state.require_processor(variant)
    return state


def save_state(raw: bytes) -> str:
    state_id = hashlib.sha256(raw).hexdigest()
    folder = state_directory()
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=folder) as temp:
        staged = Path(temp) / "state"
        staged.write_bytes(raw)
        os.replace(staged, folder / f"{state_id}.state")
    return state_id


def run_host(
    variant: Variant, state: SerumState | None = None, render: bool = False,
    audition: AuditionInput | None = None,
) -> tuple[bytes, bytes | None]:
    if platform.system() != "Darwin":
        raise SerumError("The Serum processor host requires macOS.")
    default_host = Path.home() / ".cache" / "ableton-mcp" / "serum-host"
    host = Path(os.environ.get("ABLETON_MCP_SERUM_HOST", default_host)).expanduser()
    plug = Path(os.environ.get("ABLETON_MCP_SERUM_VST3", BUNDLE)).expanduser()
    if not host.is_absolute() or not host.is_file() or not os.access(host, os.X_OK):
        raise SerumError("Set ABLETON_MCP_SERUM_HOST to the absolute path of the compiled Serum processor probe.")
    if not plug.is_absolute() or not plug.is_dir():
        raise SerumError("Serum2.vst3 was not found at the configured path.")
    try:
        with (plug / "Contents" / "Info.plist").open("rb") as file:
            identity = plistlib.load(file)
    except (OSError, plistlib.InvalidFileException) as exc:
        raise SerumError("Cannot read the Serum VST3 bundle version.") from exc
    if identity.get("CFBundleShortVersionString") != SUPPORTED_BUILD:
        raise SerumError(f"Serum state editing currently requires build {SUPPORTED_BUILD}.")
    with tempfile.TemporaryDirectory(prefix="ableton-mcp-serum-") as temp:
        folder = Path(temp)
        input_path, output_path, audio_path = (folder / name for name in ("input", "output", "audio"))
        if state is not None:
            state.require_processor(variant)
            input_path.write_bytes(encode_state(state))
        args = [str(host), str(plug), variant, str(input_path) if state else "-",
                str(output_path), str(audio_path) if render else "-"]
        if state is not None and any(
            isinstance(state.data.get(f"Oscillator{index}"), dict)
            and isinstance(state.data[f"Oscillator{index}"].get("plainParams"), dict)
            and state.data[f"Oscillator{index}"]["plainParams"].get("kParamType") == "kOsc_MultiSample"
            for index in range(3)
        ):
            args.append("--wait-for-samples")
        frames = DEFAULT_RENDER_FRAMES
        if audition is not None:
            frames = audition.frames
            events_path = folder / "events.txt"
            events_path.write_text(audition.events, encoding="ascii")
            args.extend(("--audition", str(events_path)))
            if audition.audio is not None:
                stimulus = folder / "stimulus.f32"
                stimulus.write_bytes(audition.audio)
                args.extend(("--audio-input", str(stimulus)))
        try:
            result = subprocess.run(
                args,
                cwd=folder, capture_output=True, text=True, errors="replace", timeout=20,
            )
        except subprocess.TimeoutExpired as exc:
            raise SerumError("Serum processor did not respond within 20 seconds.") from exc
        except OSError as exc:
            raise SerumError(f"Could not start the Serum processor host: {exc}") from exc
        if result.returncode:
            reason = result.stderr.strip()[-300:]
            raise SerumError(f"Serum processor host exited with {result.returncode}. {reason}".rstrip())
        try:
            raw = read_limited(output_path)
            audio = read_limited(audio_path, frames * 8) if render else None
        except FileNotFoundError as exc:
            raise SerumError("Serum processor host did not write its result.") from exc
    decode_state(raw).require_processor(variant)
    if render and (audio is None or len(audio) != frames * 8):
        raise SerumError("Incomplete Serum probe render.")
    return raw, audio


def initial_state(variant: Variant, state_id: str | None = None) -> SerumState:
    if state_id is not None:
        return read_saved_state(state_id, variant)
    return decode_state(run_host(variant)[0])
