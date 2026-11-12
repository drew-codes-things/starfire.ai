from __future__ import annotations

import asyncio
import fnmatch
import io
import re
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SPEED = 1.0
MIN_SPEED = 0.5
MAX_SPEED = 2.0
MAX_SPOKEN_CHARS = 600
VOICE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}\.onnx$")


@dataclass(frozen=True)
class VoiceRule:
    pattern: str
    voice_file: str
    speed: float


class VoiceError(ValueError):
    pass


class VoiceUnavailable(RuntimeError):
    pass


def parse_speed(text: str, rule: str) -> float:
    try:
        speed = float(text)
    except ValueError as error:
        raise VoiceError(f"model voice {rule!r}: speed {text!r} isn't a number") from error
    if not MIN_SPEED <= speed <= MAX_SPEED:
        raise VoiceError(f"model voice {rule!r}: speed must be between {MIN_SPEED} and {MAX_SPEED}")
    return speed


def parse_rule(rule: str) -> VoiceRule:
    pattern, separator, voice = rule.partition("=")
    if not separator or not pattern.strip() or not voice.strip():
        raise VoiceError(f"model voice {rule!r} should look like pattern=voice.onnx or pattern=voice.onnx@0.9")
    voice_file, _, speed_text = voice.strip().partition("@")
    if not VOICE_FILE.match(voice_file):
        raise VoiceError(f"model voice {rule!r}: {voice_file!r} must be a .onnx file name inside the voices folder")
    speed = parse_speed(speed_text, rule) if speed_text else DEFAULT_SPEED
    return VoiceRule(pattern.strip(), voice_file, speed)


def parse_rules(setting: str) -> list[VoiceRule]:
    return [parse_rule(rule.strip()) for rule in setting.split(",") if rule.strip()]


def rule_for(model: str, rules: list[VoiceRule]) -> VoiceRule | None:
    return next((rule for rule in rules if fnmatch.fnmatchcase(model, rule.pattern)), None)


def spoken_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split())
    if not cleaned:
        raise VoiceError("there is nothing to say")
    if len(cleaned) > MAX_SPOKEN_CHARS:
        raise VoiceError(f"say at most {MAX_SPOKEN_CHARS} characters at a time")
    return cleaned


class VoiceCache:
    def __init__(self):
        self._voices = {}
        self._lock = threading.Lock()

    def voice(self, path: Path):
        with self._lock:
            if path not in self._voices:
                from piper import PiperVoice
                self._voices[path] = PiperVoice.load(path)
            return self._voices[path]


voices = VoiceCache()


def synthesize(text: str, path: Path, speed: float) -> bytes:
    if not path.is_file():
        raise VoiceUnavailable(f"voice file {path.name} isn't in the voices folder")
    from piper import SynthesisConfig
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        voices.voice(path).synthesize_wav(text, writer, SynthesisConfig(length_scale=1 / speed))
    return buffer.getvalue()


async def speak(text: str, rule: VoiceRule, voices_dir: str) -> bytes:
    path = Path(voices_dir) / rule.voice_file
    return await asyncio.to_thread(synthesize, spoken_text(text), path, rule.speed)
