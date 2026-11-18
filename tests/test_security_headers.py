import os
import sys

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server_configs"))

import security_headers


def build_app():
    app = FastAPI()

    @app.get("/login.html")
    def login_page():
        return PlainTextResponse("<!DOCTYPE html>", media_type="text/html")

    @app.get("/api/file")
    def stored_file():
        return PlainTextResponse("x", headers={"Content-Security-Policy": "sandbox"})

    security_headers.install(app)
    return TestClient(app)


def test_every_page_refuses_framing_sniffing_and_cross_site_referrers():
    headers = build_app().get("/login.html").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    assert headers["referrer-policy"] == "same-origin"


def directives(policy):
    return {part.split()[0]: part.split()[1:] for part in policy.split("; ")}


def test_pages_may_only_load_scripts_images_and_connections_from_starfire_itself():
    policy = directives(build_app().get("/login.html").headers["content-security-policy"])
    assert policy["default-src"] == ["'self'"] and policy["connect-src"] == ["'self'"]
    assert policy["img-src"] == ["'self'", "data:", "blob:"] and policy["object-src"] == ["'none'"]
    assert policy["script-src"][0] == "'self'" and "'unsafe-inline'" not in policy["script-src"]
    assert policy["base-uri"] == ["'none'"] and policy["form-action"] == ["'self'"]


def test_the_inline_theme_scripts_are_allowed_by_their_exact_hash_and_nothing_else():
    static = os.path.join(ROOT, "static")
    hashes = security_headers.inline_script_hashes(static)
    assert len(hashes) == len(set(hashes)) >= 1
    policy = directives(security_headers.content_security_policy(static))
    assert policy["script-src"] == ["'self'", *hashes]
