import asyncio
import os
import stat
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import httpx
import pytest

import piper_tts

FAKE_PIPER = """#!{python}
import os, sys, time
args = sys.argv[1:]
out_path = args[args.index("--output_file") + 1]
model = args[args.index("--model") + 1]
text = sys.stdin.read()
record = os.environ["FAKE_PIPER_RECORD"]
with open(record, "w") as f:
    f.write(f"{{os.getpid()}}\\n{{out_path}}\\n{{model}}\\n")
mode = os.environ.get("FAKE_PIPER_MODE", "ok")
if mode == "fail":
    sys.stderr.write("Invalid voice model: unexpected end of file")
    sys.exit(1)
if mode == "hang":
    time.sleep(30)
with open(out_path, "wb") as f:
    f.write(b"RIFF" + text.encode())
"""


@pytest.fixture
def fake_piper(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "piper"
    script.write_text(FAKE_PIPER.format(python=sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    record = tmp_path / "piper-run.txt"
    voice = tmp_path / "en_GB-alan-medium.onnx"
    voice.write_bytes(b"voice")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("FAKE_PIPER_RECORD", str(record))

    def last_run():
        pid, out_path, model = record.read_text().splitlines()
        return int(pid), out_path, model

    return voice, last_run


def test_piper_speaks_the_text_with_the_chosen_voice_and_cleans_up(fake_piper):
    voice, last_run = fake_piper
    assert piper_tts.is_installed()
    assert asyncio.run(piper_tts.generate("Good morning", str(voice))) == b"RIFFGood morning"
    _, out_path, model = last_run()
    assert model == str(voice)
    assert not os.path.exists(out_path)


def test_a_piper_failure_reports_what_piper_said(fake_piper, monkeypatch):
    voice, last_run = fake_piper
    monkeypatch.setenv("FAKE_PIPER_MODE", "fail")
    with pytest.raises(RuntimeError, match="piper failed: Invalid voice model: unexpected end of file"):
        asyncio.run(piper_tts.generate("Good morning", str(voice)))
    assert not os.path.exists(last_run()[1])


def test_speech_without_piper_installed_says_how_to_get_it(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))
    assert not piper_tts.is_installed()
    with pytest.raises(RuntimeError, match="piper is not installed"):
        asyncio.run(piper_tts.generate("Hello", str(tmp_path / "voice.onnx")))


def test_speech_with_a_missing_voice_file_names_it(fake_piper, tmp_path):
    missing = tmp_path / "gone.onnx"
    with pytest.raises(RuntimeError, match=f"voice model not found: {missing}"):
        asyncio.run(piper_tts.generate("Hello", str(missing)))


@pytest.mark.parametrize("quality, expected", [("high", "high"), ("x-low", "medium"), ("", "medium")])
def test_voice_downloads_point_at_the_piper_voices_repository(quality, expected):
    onnx, config = piper_tts.voice_url("en_GB-alan", quality)
    base = f"{piper_tts.HF_VOICES_BASE}/en/en_GB/alan/{expected}/en_GB-alan-{expected}"
    assert (onnx, config) == (base + ".onnx", base + ".onnx.json")


@pytest.fixture
def voice_host(monkeypatch):
    asked = []

    def handle(request):
        asked.append(request.url.path)
        if request.url.path.endswith(".onnx"):
            return httpx.Response(200, content=b"v" * 2048, headers={"content-length": "2048"})
        return httpx.Response(200, content=b'{"audio": {"sample_rate": 22050}}')

    real_client = httpx.AsyncClient
    monkeypatch.setattr(piper_tts.httpx, "AsyncClient",
                        lambda *args, **kwargs: real_client(*args, transport=httpx.MockTransport(handle), **kwargs))
    return asked


def pull(name, quality, folder):
    async def collect():
        return [progress async for progress in piper_tts.pull_voice(name, quality, folder)]
    return asyncio.run(collect())


def test_a_voice_download_saves_the_model_and_its_config_with_progress(voice_host, tmp_path):
    progress = pull("en_GB-alan", "low", str(tmp_path))
    assert progress[-1] == (2048, 2048)
    assert (tmp_path / "en_GB-alan-low.onnx").read_bytes() == b"v" * 2048
    assert (tmp_path / "en_GB-alan-low.onnx.json").read_bytes() == b'{"audio": {"sample_rate": 22050}}'
    assert not (tmp_path / "en_GB-alan-low.onnx.part").exists()


def test_a_voice_download_into_a_missing_folder_is_refused(voice_host, tmp_path):
    with pytest.raises(RuntimeError, match="voices directory does not exist"):
        pull("en_GB-alan", "low", str(tmp_path / "missing"))
    assert voice_host == []
