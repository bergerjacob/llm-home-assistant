"""Audio validation and encoding utilities for the audio-direct pipeline."""
from __future__ import annotations

import base64
import logging

_LOGGER = logging.getLogger(__name__)

SUPPORTED_FORMATS = {"wav", "mp3", "flac", "opus", "pcm16", "webm", "ogg"}
MAX_AUDIO_SIZE = 15 * 1024 * 1024  # 15 MiB


def normalize_format(fmt: str) -> str:
    """Strip leading dots, lowercase."""
    return fmt.lstrip(".").lower()


def validate_audio(data: bytes, fmt: str) -> None:
    """Raise ValueError if audio data is invalid."""
    if not data:
        raise ValueError("Audio data is empty")

    fmt = normalize_format(fmt)
    if fmt not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Unsupported audio format '{fmt}'. "
            f"Supported: {', '.join(sorted(SUPPORTED_FORMATS))}"
        )

    if len(data) > MAX_AUDIO_SIZE:
        raise ValueError(
            f"Audio file too large ({len(data)} bytes). "
            f"Max allowed: {MAX_AUDIO_SIZE} bytes (15 MiB)"
        )


def encode_audio_base64(data: bytes) -> str:
    """Return a base64-encoded string of raw audio bytes."""
    return base64.b64encode(data).decode("ascii")
