import base64
import hashlib
import os
import re

from fastapi import FastAPI, Request

PAGES_WITH_INLINE_SCRIPTS = ("index.html", "login.html")
INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
BROWSER_HARDENING = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}


def inline_script_hashes(static_dir: str) -> list[str]:
    hashes = []
    for page in PAGES_WITH_INLINE_SCRIPTS:
        path = os.path.join(static_dir, page)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for script in INLINE_SCRIPT.findall(f.read()):
                digest = base64.b64encode(hashlib.sha256(script.encode("utf-8")).digest()).decode()
                hashes.append(f"'sha256-{digest}'")
    return list(dict.fromkeys(hashes))


def content_security_policy(static_dir: str | None) -> str:
    scripts = " ".join(["'self'", *(inline_script_hashes(static_dir) if static_dir else [])])
    return "; ".join([
        "default-src 'self'",
        f"script-src {scripts}",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob:",
        "media-src 'self' data: blob:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ])


def install(app: FastAPI, static_dir: str | None = None) -> None:
    headers = {**BROWSER_HARDENING, "Content-Security-Policy": content_security_policy(static_dir)}

    @app.middleware("http")
    async def harden_responses(request: Request, call_next):
        response = await call_next(request)
        for name, value in headers.items():
            response.headers.setdefault(name, value)
        return response
