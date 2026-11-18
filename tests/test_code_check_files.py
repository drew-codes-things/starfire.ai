import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import code_check
import host_sandbox

needs_cc = pytest.mark.skipif(code_check._c_compiler() is None, reason="no C compiler installed")
needs_sandbox = pytest.mark.skipif(host_sandbox.working_sandbox_program() is None,
                                   reason="bubblewrap is not installed or cannot start here")
SECRET = "9gqW9J_SCC-944w8HsfAwjevR-1SCu3cwfYmRvdYJeU="
TRICKS = [
    '#/**/include "{path}"',
    '/**/#include "{path}"',
    '#\finclude "{path}"',
    '\v#include "{path}"',
    'char *x[] = \n#/**/include "{path}"',
    '#if __has_include("{path}")\n#error present\n#endif',
]


@pytest.fixture
def server(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    key = data / ".key"
    key.write_text(SECRET + "\n")
    outside = tmp_path / "outside.txt"
    outside.write_text(f"other_file_marker {SECRET}\n")
    return {"data": data, "key": key, "outside": outside}


@needs_cc
@pytest.mark.parametrize("trick", TRICKS)
@pytest.mark.parametrize("target", ["key", "outside"])
def test_no_include_trick_shows_another_files_contents(server, trick, target):
    code = trick.format(path=server[target]) + "\nint main(void) { return 0; }\n"
    result = code_check.check_sync("c", code, (str(server["data"]),))
    assert SECRET not in result["message"] and "other_file_marker" not in result["message"]
    assert "present" not in result["message"] or target == "outside"


@needs_cc
def test_without_the_sandbox_c_is_not_compiled_at_all(server, monkeypatch):
    ran = []
    monkeypatch.setattr(host_sandbox, "sandbox_program", lambda: None)
    monkeypatch.setattr(code_check, "working_sandbox_program", lambda: None)
    monkeypatch.setattr(code_check, "_run", lambda *args, **kwargs: ran.append(args) or (0, ""))
    result = code_check.check_sync("c", "#include <stdio.h>\nint main(void) { return 0; }\n")
    assert result["status"] == "skipped" and result["message"] == code_check.NEEDS_SANDBOX and ran == []


def test_a_runner_that_needs_the_sandbox_refuses_to_start_without_it(monkeypatch, tmp_path):
    monkeypatch.setattr(code_check.shutil, "which", lambda name: "/usr/bin/prlimit" if name == "prlimit" else None)
    monkeypatch.setattr(code_check, "working_sandbox_program", lambda: None)
    with pytest.raises(code_check.RunnerUnavailable, match="bubblewrap"):
        code_check.runner_argv(["cc", "-fsyntax-only", "a.c"], str(tmp_path), (), code_check.RUNNER_LIMITS,
                               needs_sandbox=True)
    assert code_check.runner_argv(["node", "--check", "a.js"], str(tmp_path), (), code_check.RUNNER_LIMITS)[0] == \
        "/usr/bin/prlimit"


@needs_cc
@needs_sandbox
def test_a_real_mistake_in_the_snippet_is_still_reported_with_its_line():
    result = code_check.check_sync("c", "#include <stdio.h>\nint main(void) { return 0 }\n")
    assert result["status"] == "error" and result["line"] == 2 and "snippet:2" in result["message"]


@needs_cc
@needs_sandbox
def test_the_compiler_cannot_see_the_hidden_folders(server):
    code = f'#if __has_include("{server["key"]}")\n#error present\n#endif\nint main(void) {{ return 0; }}\n'
    assert code_check.check_sync("c", code, (str(server["data"]),))["status"] == "ok"


def test_messages_about_other_files_are_dropped_but_the_snippets_own_are_kept():
    message = ("In file included from snippet:1:\n/srv/starfire/data/.key:1:1: error: invalid suffix\n"
               f"    1 | {SECRET}\n      | ^~~~\nsnippet:3: error: expected ';'\n    3 | int x\n      |      ^")
    shown = code_check._snippet_messages_only(message)
    assert SECRET not in shown and "/srv/starfire" not in shown
    assert "snippet:3: error: expected ';'" in shown and "int x" in shown
    assert shown.endswith(code_check.INCLUDED_FILE_NOTE)


@pytest.fixture
def include_filter_off(monkeypatch):
    monkeypatch.setattr(code_check, "_reads_files_outside_system_headers", lambda code: False)


@pytest.fixture
def data_outside_tmp():
    with tempfile.TemporaryDirectory(dir=ROOT, prefix=".hidden-check-") as data:
        key = os.path.join(data, ".key")
        with open(key, "w", encoding="utf-8") as handle:
            handle.write(SECRET + "\n")
        yield data, key


@needs_cc
@needs_sandbox
def test_a_file_inside_a_hidden_folder_does_not_exist_for_the_compiler(data_outside_tmp, include_filter_off):
    data, key = data_outside_tmp
    result = code_check.check_sync("c", f'#include "{key}"\nint main(void) {{ return 0; }}\n', (data,))
    assert result["status"] == "error" and SECRET not in result["message"]
    assert "No such file" in result["message"] or "file not found" in result["message"]


@needs_cc
@needs_sandbox
def test_compiler_messages_quoting_another_file_are_dropped_from_a_real_check(server, include_filter_off):
    result = code_check.check_sync("c", f'#include "{server["outside"]}"\nint main(void) {{ return 0; }}\n',
                                   (str(server["data"]),))
    assert result["status"] == "error" and result["message"].endswith(code_check.INCLUDED_FILE_NOTE)
    assert SECRET not in result["message"] and "other_file_marker" not in result["message"]
