import os
import sys
import wave
import io

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import model_voices


def test_rules_read_a_pattern_a_voice_file_and_an_optional_speed():
    rules = model_voices.parse_rules("own/*=en_GB-jenny_dioco-medium.onnx@1.1, Arty=en_GB-alan-medium.onnx")
    assert rules == [model_voices.VoiceRule("own/*", "en_GB-jenny_dioco-medium.onnx", 1.1),
                     model_voices.VoiceRule("Arty", "en_GB-alan-medium.onnx", 1.0)]
    assert model_voices.parse_rules("") == []


@pytest.mark.parametrize("setting", ["own/*", "=voice.onnx", "own/*=../voice.onnx", "own/*=/etc/voice.onnx",
                                     "own/*=voice.wav", "own/*=voice.onnx@fast", "own/*=voice.onnx@9"])
def test_bad_rules_are_refused_with_the_reason(setting):
    with pytest.raises(model_voices.VoiceError, match="model voice"):
        model_voices.parse_rules(setting)


def test_the_first_matching_rule_gives_the_model_its_voice():
    rules = model_voices.parse_rules("own/*=a.onnx,*=b.onnx")
    assert model_voices.rule_for("own/Nova", rules).voice_file == "a.onnx"
    assert model_voices.rule_for("qwen3:14b", rules).voice_file == "b.onnx"
    assert model_voices.rule_for("own/Nova", model_voices.parse_rules("Arty=a.onnx")) is None


def test_spoken_text_is_one_tidy_line_of_limited_length():
    assert model_voices.spoken_text("  Hello\n  there. ") == "Hello there."
    with pytest.raises(model_voices.VoiceError):
        model_voices.spoken_text("   ")
    with pytest.raises(model_voices.VoiceError):
        model_voices.spoken_text("a" * (model_voices.MAX_SPOKEN_CHARS + 1))


def test_a_missing_voice_file_is_reported(tmp_path):
    with pytest.raises(model_voices.VoiceUnavailable, match="gone.onnx"):
        model_voices.synthesize("Hello.", tmp_path / "gone.onnx", 1.0)


class FakeVoice:
    def __init__(self):
        self.calls = []

    def synthesize_wav(self, text, writer, config):
        self.calls.append((text, config.length_scale))
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(22050)
        writer.writeframes(b"\0\0" * 2205)


def test_speaking_uses_the_cached_voice_at_the_rule_speed(tmp_path, monkeypatch):
    piper = pytest.importorskip("piper")
    assert piper
    (tmp_path / "a.onnx").write_bytes(b"model")
    fake = FakeVoice()
    loads = []
    monkeypatch.setattr(model_voices, "voices", model_voices.VoiceCache())
    monkeypatch.setattr("piper.PiperVoice.load", lambda path: loads.append(path) or fake)
    rule = model_voices.VoiceRule("own/*", "a.onnx", 1.25)
    import asyncio
    first = asyncio.run(model_voices.speak("Hi  there.", rule, str(tmp_path)))
    asyncio.run(model_voices.speak("Again.", rule, str(tmp_path)))
    with wave.open(io.BytesIO(first)) as reader:
        assert reader.getnframes() == 2205
    assert fake.calls == [("Hi there.", 0.8), ("Again.", 0.8)]
    assert loads == [tmp_path / "a.onnx"]
