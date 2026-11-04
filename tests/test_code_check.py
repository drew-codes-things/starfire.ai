import asyncio
import os
import shutil
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import code_check

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is not installed")
needs_cc = pytest.mark.skipif(code_check._c_compiler() is None, reason="no C compiler installed")


def test_language_aliases_map_to_one_checker():
    assert code_check.canonical_language("Py") == "python"
    assert code_check.canonical_language("sh") == "shell"
    assert code_check.canonical_language("c++") == "cpp"
    assert code_check.canonical_language("brainfuck") is None


def test_python_syntax_ok():
    result = code_check.check_sync("python", "def f():\n    return 1\n")
    assert result["status"] == "ok"
    assert result["checker"] == "python compile"


def test_python_syntax_error_reports_the_line():
    result = code_check.check_sync("python", "x = 1\ndef f(:\n    pass\n")
    assert result["status"] == "error"
    assert result["line"] == 2
    assert "line 2" in result["message"]


def test_python_check_never_runs_the_code(tmp_path):
    marker = tmp_path / "ran"
    result = code_check.check_sync("python", f"open({str(marker)!r}, 'w').write('x')\n")
    assert result["status"] == "ok"
    assert not marker.exists()


def test_json_and_toml_and_xml():
    assert code_check.check_sync("json", '{"a": [1, 2]}')["status"] == "ok"
    bad = code_check.check_sync("json", '{"a": 1,}')
    assert bad["status"] == "error" and bad["line"] == 1
    assert code_check.check_sync("toml", "a = 1\n[b]\nc = 'd'\n")["status"] == "ok"
    assert code_check.check_sync("toml", "a =\n")["status"] == "error"
    assert code_check.check_sync("xml", "<a><b/></a>")["status"] == "ok"
    assert code_check.check_sync("xml", "<a><b></a>")["status"] == "error"


def test_unknown_or_empty_blocks_are_skipped():
    assert code_check.check_sync("", "whatever")["status"] == "skipped"
    assert code_check.check_sync("rust", "fn main() {}")["status"] == "skipped"
    assert code_check.check_sync("python", "   \n")["status"] == "skipped"


def test_oversized_blocks_are_skipped():
    result = code_check.check_sync("python", "x = 1\n" * (code_check.MAX_CODE_CHARS // 4))
    assert result["status"] == "skipped"


@needs_node
def test_javascript_including_es_modules():
    assert code_check.check_sync("js", "const a = 1;\nconsole.log(a);\n")["status"] == "ok"
    assert code_check.check_sync("javascript", "import fs from 'fs';\nexport const x = fs;\n")["status"] == "ok"
    bad = code_check.check_sync("js", "const a = 1;\nconst b = ;\n")
    assert bad["status"] == "error"
    assert bad["line"] == 2
    assert "SyntaxError" in bad["message"]


@needs_bash
def test_shell_syntax():
    assert code_check.check_sync("bash", "for i in 1 2; do echo $i; done\n")["status"] == "ok"
    bad = code_check.check_sync("sh", "if [ 1 ]; then echo hi\n")
    assert bad["status"] == "error"
    assert bad["message"]


def test_async_wrapper_returns_same_shape():
    result = asyncio.run(code_check.check("python", "x = 1\n"))
    assert result["status"] == "ok"


@needs_cc
def test_c_with_system_headers_is_checked():
    assert code_check.check_sync("c", "#include <stdio.h>\nint main(void) { puts(\"hi\"); return 0; }\n")["status"] == "ok"
    assert code_check.check_sync("cpp", "#include <vector>\nint main() { std::vector<int> v; return 0; }\n")["status"] == "ok"
    assert code_check.check_sync("c", "#include <sys/types.h>\nint main(void) { return 0 }\n")["status"] == "error"


@needs_cc
@pytest.mark.parametrize("directive", [
    '#include "{path}"',
    "#include <{path}>",
    '  #  include "{path}"',
    '%:include "{path}"',
    '#inc\\\nlude "{path}"',
    '#include_next "{path}"',
    '#import "{path}"',
    '#embed "{path}"',
    "#include <../../../../../../..{path}>",
    '#define LOCATION "{path}"\n#include LOCATION',
])
def test_c_never_reads_files_outside_system_headers(tmp_path, directive):
    secret = tmp_path / "secret.txt"
    secret.write_text("leaked_marker_value = 1\n")
    code = directive.format(path=secret) + "\nint main(void) { return 0; }\n"
    result = code_check.check_sync("c", code)
    assert result["status"] == "skipped"
    assert "leaked_marker_value" not in result["message"]
