import asyncio
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import xml.etree.ElementTree as ET

CHECK_TIMEOUT_S = 20
MAX_CODE_CHARS = 200_000

LANGUAGE_ALIASES = {
    "python": "python", "py": "python", "python3": "python",
    "javascript": "javascript", "js": "javascript", "node": "javascript", "nodejs": "javascript",
    "mjs": "javascript", "cjs": "javascript",
    "typescript": "typescript", "ts": "typescript",
    "bash": "shell", "sh": "shell", "shell": "shell", "zsh": "shell",
    "json": "json",
    "toml": "toml",
    "xml": "xml", "svg": "xml",
    "go": "go", "golang": "go",
    "c": "c",
    "cpp": "cpp", "c++": "cpp", "cc": "cpp", "cxx": "cpp",
    "ruby": "ruby", "rb": "ruby",
    "php": "php",
    "yaml": "yaml", "yml": "yaml",
}

NODE_MODULE_HINTS = ("Cannot use import statement outside a module", "Unexpected token 'export'",
                     "Cannot use 'import.meta' outside a module")


def canonical_language(language: str) -> str | None:
    return LANGUAGE_ALIASES.get((language or "").strip().lower())


def _result(language: str, status: str, checker: str, message: str = "", line: int | None = None) -> dict:
    return {"language": language, "status": status, "checker": checker, "message": message, "line": line}


def _first_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def _run(cmd: list[str], cwd: str | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=CHECK_TIMEOUT_S, cwd=cwd,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 124, f"{cmd[0]} timed out after {CHECK_TIMEOUT_S}s"
    except OSError as e:
        return 127, str(e)
    output = (proc.stderr or "") + ("\n" if proc.stderr and proc.stdout else "") + (proc.stdout or "")
    return proc.returncode, output.strip()


def _write_temp(code: str, suffix: str, prefix: str = "") -> tuple[str, str]:
    directory = tempfile.mkdtemp(prefix="starfire-check-")
    path = os.path.join(directory, f"snippet{suffix}")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(prefix + code)
        if not code.endswith("\n"):
            fh.write("\n")
    return directory, path


NOISE_PREFIXES = ("    at ", "Node.js v", "(Use `node")
LINE_PATTERN = r"(?:snippet(?:\.\w+)?:|line )(\d+)"


def _tidy(output: str, path: str, directory: str) -> str:
    cleaned = output.replace(path, "snippet").replace(directory + os.sep, "").replace(directory, "")
    lines = [line.rstrip() for line in cleaned.splitlines()
             if line.strip() and not line.startswith(NOISE_PREFIXES)]
    return "\n".join(lines[:12])


def _check_python(code: str) -> dict:
    try:
        compile(code, "snippet.py", "exec")
    except SyntaxError as e:
        detail = f"line {e.lineno}: {e.msg}" if e.lineno else e.msg
        return _result("python", "error", "python compile", detail, e.lineno)
    except ValueError as e:
        return _result("python", "error", "python compile", str(e))
    return _result("python", "ok", "python compile")


def _check_json(code: str) -> dict:
    try:
        json.loads(code)
    except json.JSONDecodeError as e:
        return _result("json", "error", "json parse", f"line {e.lineno}: {e.msg}", e.lineno)
    return _result("json", "ok", "json parse")


def _check_toml(code: str) -> dict:
    try:
        tomllib.loads(code)
    except tomllib.TOMLDecodeError as e:
        return _result("toml", "error", "toml parse", str(e), _first_int(r"line (\d+)", str(e)))
    return _result("toml", "ok", "toml parse")


def _check_xml(code: str) -> dict:
    try:
        ET.fromstring(code)
    except ET.ParseError as e:
        line = e.position[0] if getattr(e, "position", None) else None
        return _result("xml", "error", "xml parse", f"line {line}: {e}" if line else str(e), line)
    return _result("xml", "ok", "xml parse")


def _check_yaml(code: str) -> dict:
    try:
        import yaml
    except ImportError:
        return _result("yaml", "skipped", "yaml parse", "PyYAML is not installed")
    try:
        list(yaml.safe_load_all(code))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 1 if mark else None
        problem = getattr(e, "problem", None) or str(e)
        return _result("yaml", "error", "yaml parse", f"line {line}: {problem}" if line else problem, line)
    return _result("yaml", "ok", "yaml parse")


def _tool_check(language: str, checker: str, tool: str, suffix: str, code: str, argv,
                prefix: str = "", retry=None) -> dict:
    binary = shutil.which(tool)
    if not binary:
        return _result(language, "skipped", checker, f"{tool} is not installed")
    directory, path = _write_temp(code, suffix, prefix)
    try:
        rc, output = _run(argv(binary, path), cwd=directory)
        if rc != 0 and retry:
            retried = retry(binary, directory, code, output)
            if retried is not None:
                rc, output, path = retried
        if rc == 0:
            return _result(language, "ok", checker)
        message = _tidy(output, path, directory)
        return _result(language, "error", checker, message, _first_int(LINE_PATTERN, message))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _check_javascript(code: str) -> dict:
    def retry(binary, directory, code, output):
        if not any(hint in output for hint in NODE_MODULE_HINTS):
            return None
        path = os.path.join(directory, "snippet.mjs")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(code)
        rc, out = _run([binary, "--check", path], cwd=directory)
        return rc, out, path

    return _tool_check("javascript", "node --check", "node", ".js", code,
                       lambda b, p: [b, "--check", p], retry=retry)


STRIP_TYPES_SCRIPT = (
    "const fs = require('node:fs');"
    "const { stripTypeScriptTypes } = require('node:module');"
    "if (typeof stripTypeScriptTypes !== 'function') { process.stderr.write('unsupported'); process.exit(3); }"
    "const [src, dest] = process.argv.slice(1);"
    "fs.writeFileSync(dest, stripTypeScriptTypes(fs.readFileSync(src, 'utf8'), { mode: 'strip' }));"
)


def _check_typescript(code: str) -> dict:
    binary = shutil.which("node")
    if not binary:
        return _result("typescript", "skipped", "node --check", "node is not installed")
    directory, path = _write_temp(code, ".ts")
    stripped = os.path.join(directory, "snippet.mjs")
    try:
        rc, output = _run([binary, "--no-warnings", "-e", STRIP_TYPES_SCRIPT, path, stripped], cwd=directory)
        if rc == 3:
            return _result("typescript", "skipped", "node --check", "needs Node.js 22.13 or newer")
        if rc == 0:
            rc, output = _run([binary, "--check", stripped], cwd=directory)
        if rc == 0:
            return _result("typescript", "ok", "node --check")
        message = _tidy(output, path, directory).replace("snippet.mjs", "snippet")
        if message.startswith(":"):
            message = "snippet" + message
        return _result("typescript", "error", "node --check", message, _first_int(LINE_PATTERN, message))
    finally:
        shutil.rmtree(directory, ignore_errors=True)


def _check_shell(code: str) -> dict:
    return _tool_check("shell", "bash -n", "bash", ".sh", code, lambda b, p: [b, "-n", p])


def _check_go(code: str) -> dict:
    def retry(binary, directory, code, output):
        if "expected 'package'" not in output:
            return None
        path = os.path.join(directory, "snippet.go")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("package main\n\n" + code)
        rc, out = _run([binary, "-e", path], cwd=directory)
        return rc, out, path

    return _tool_check("go", "gofmt -e", "gofmt", ".go", code, lambda b, p: [b, "-e", p], retry=retry)


LINE_SPLICE = re.compile(r"\\\r?\n")
FILE_DIRECTIVE = re.compile(r"^[ \t]*(?:#|%:)[ \t]*(include_next|include|import|embed)\b[ \t]*(.*)$", re.M)
SYSTEM_HEADER = re.compile(r"<([A-Za-z0-9_][A-Za-z0-9_+./-]*)>")


def _is_system_header(directive: str, argument: str) -> bool:
    if directive != "include":
        return False
    match = SYSTEM_HEADER.fullmatch(argument.strip())
    return bool(match) and ".." not in match.group(1)


def _reads_files_outside_system_headers(code: str) -> bool:
    spliced = LINE_SPLICE.sub("", code)
    return any(not _is_system_header(m.group(1), m.group(2)) for m in FILE_DIRECTIVE.finditer(spliced))


def _c_compiler() -> str | None:
    for tool in ("gcc", "clang", "cc"):
        if shutil.which(tool):
            return tool
    return None


def _check_c_family(language: str, code: str) -> dict:
    tool = _c_compiler()
    if not tool:
        return _result(language, "skipped", "cc -fsyntax-only", "no C compiler installed")
    if _reads_files_outside_system_headers(code):
        return _result(language, "skipped", f"{tool} -fsyntax-only",
                       "only code whose #include lines name system headers like <stdio.h> is checked")
    suffix = ".c" if language == "c" else ".cpp"
    return _tool_check(language, f"{tool} -fsyntax-only", tool, suffix, code,
                       lambda b, p: [b, "-fsyntax-only", "-w", p])


def _check_ruby(code: str) -> dict:
    return _tool_check("ruby", "ruby -c", "ruby", ".rb", code, lambda b, p: [b, "-c", p])


def _check_php(code: str) -> dict:
    prefix = "" if code.lstrip().startswith("<?") else "<?php\n"
    return _tool_check("php", "php -l", "php", ".php", code, lambda b, p: [b, "-l", p], prefix=prefix)


CHECKERS = {
    "python": _check_python,
    "javascript": _check_javascript,
    "typescript": _check_typescript,
    "shell": _check_shell,
    "json": _check_json,
    "toml": _check_toml,
    "xml": _check_xml,
    "yaml": _check_yaml,
    "go": _check_go,
    "c": lambda code: _check_c_family("c", code),
    "cpp": lambda code: _check_c_family("cpp", code),
    "ruby": _check_ruby,
    "php": _check_php,
}


def check_sync(language: str, code: str) -> dict:
    canonical = canonical_language(language)
    if not canonical:
        return _result((language or "").strip().lower(), "skipped", "", "no checker for this language")
    if not code.strip():
        return _result(canonical, "skipped", "", "empty code block")
    if len(code) > MAX_CODE_CHARS:
        return _result(canonical, "skipped", "", "code block too large to check")
    if "\x00" in code:
        return _result(canonical, "error", "", "code contains a null byte")
    try:
        return CHECKERS[canonical](code)
    except Exception as e:
        return _result(canonical, "skipped", "", f"checker failed: {e}")


async def check(language: str, code: str) -> dict:
    return await asyncio.to_thread(check_sync, language, code)
