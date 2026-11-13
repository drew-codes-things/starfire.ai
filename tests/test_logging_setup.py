import os
import stat
import subprocess
import sys
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

UNDER_UVICORN = textwrap.dedent("""
    import logging
    import uvicorn.config
    uvicorn.config.Config("server:app").load()
    logging.getLogger("mcp_servers_store").info("app info line reaches stderr")
    logging.getLogger("routes").debug("app debug line stays quiet")
    logging.getLogger("httpx").info("third party info line with ?api_key=PLACEHOLDER stays quiet")
    logging.getLogger("httpx").warning("third party warning still shows")
    logging.getLogger("uvicorn.error").info("uvicorn line shows once")
""")


def fake_tool(folder, name: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_app_info_lines_reach_stderr_when_uvicorn_loads_the_app(tmp_path):
    tools = tmp_path / "bin"
    tools.mkdir()
    for name in ("uvx", "piper", "npx"):
        fake_tool(tools, name)
    env = dict(os.environ, PATH=f"{tools}{os.pathsep}{os.environ.get('PATH', '')}", DATA_DIR=str(tmp_path / "data"))
    finished = subprocess.run([sys.executable, "-c", UNDER_UVICORN], cwd=ROOT, env=env, capture_output=True,
                              text=True, timeout=120)
    assert finished.returncode == 0, finished.stderr
    assert "app info line reaches stderr" in finished.stderr
    assert "third party warning still shows" in finished.stderr
    assert finished.stderr.count("uvicorn line shows once") == 1
    assert "app debug line stays quiet" not in finished.stderr
    assert "PLACEHOLDER" not in finished.stderr
