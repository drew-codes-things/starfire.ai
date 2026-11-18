import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server_configs"))

import auth_routes
from auth import LOGIN_ATTEMPT_WINDOW_SECONDS, LOGIN_ATTEMPTS_PER_ADDRESS, LoginLimiter


@pytest.fixture(autouse=True)
def separate_sign_in_attempt_counts(monkeypatch):
    monkeypatch.setattr(auth_routes, "address_attempts",
                        LoginLimiter(max_failures=LOGIN_ATTEMPTS_PER_ADDRESS, lockout_seconds=LOGIN_ATTEMPT_WINDOW_SECONDS))
    monkeypatch.setattr(auth_routes, "password_change_limiter", LoginLimiter())
