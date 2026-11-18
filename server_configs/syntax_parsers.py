import json
import re
import sys
import tomllib
import xml.etree.ElementTree as ET


def result(language: str, status: str, checker: str, message: str = "", line: int | None = None) -> dict:
    return {"language": language, "status": status, "checker": checker, "message": message, "line": line}


def first_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def _check_python(code: str) -> dict:
    try:
        compile(code, "snippet.py", "exec")
    except SyntaxError as e:
        detail = f"line {e.lineno}: {e.msg}" if e.lineno else e.msg
        return result("python", "error", "python compile", detail, e.lineno)
    except ValueError as e:
        return result("python", "error", "python compile", str(e))
    return result("python", "ok", "python compile")


def _check_json(code: str) -> dict:
    try:
        json.loads(code)
    except json.JSONDecodeError as e:
        return result("json", "error", "json parse", f"line {e.lineno}: {e.msg}", e.lineno)
    return result("json", "ok", "json parse")


def _check_toml(code: str) -> dict:
    try:
        tomllib.loads(code)
    except tomllib.TOMLDecodeError as e:
        return result("toml", "error", "toml parse", str(e), first_int(r"line (\d+)", str(e)))
    return result("toml", "ok", "toml parse")


def _check_xml(code: str) -> dict:
    try:
        ET.fromstring(code)
    except ET.ParseError as e:
        line = e.position[0] if getattr(e, "position", None) else None
        return result("xml", "error", "xml parse", f"line {line}: {e}" if line else str(e), line)
    return result("xml", "ok", "xml parse")


def _check_yaml(code: str) -> dict:
    try:
        import yaml
    except ImportError:
        return result("yaml", "skipped", "yaml parse", "PyYAML is not installed")
    try:
        list(yaml.compose_all(code, Loader=yaml.SafeLoader))
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 1 if mark else None
        problem = getattr(e, "problem", None) or str(e)
        return result("yaml", "error", "yaml parse", f"line {line}: {problem}" if line else problem, line)
    return result("yaml", "ok", "yaml parse")


PARSERS = {
    "python": _check_python,
    "json": _check_json,
    "toml": _check_toml,
    "xml": _check_xml,
    "yaml": _check_yaml,
}


def main() -> int:
    language = sys.argv[1] if len(sys.argv) > 1 else ""
    if language not in PARSERS:
        sys.stderr.write(f"no parser for {language!r}\n")
        return 2
    code = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    sys.stdout.write(json.dumps(PARSERS[language](code)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
