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
    assert headers["content-security-policy"] == "frame-ancestors 'none'"
    assert headers["referrer-policy"] == "same-origin"


def test_a_route_that_sets_its_own_policy_keeps_it():
    headers = build_app().get("/api/file").headers
    assert headers["content-security-policy"] == "sandbox"
    assert headers["x-frame-options"] == "DENY"
