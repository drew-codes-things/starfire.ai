import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server_configs"))

import auth_routes
import connection_health
import routes
from auth import LOGIN_ATTEMPT_WINDOW_SECONDS, LOGIN_ATTEMPTS_PER_ADDRESS, LoginLimiter
from connection_health import ConnectionHealth


@pytest.fixture(autouse=True)
def separate_sign_in_attempt_counts(monkeypatch):
    monkeypatch.setattr(auth_routes, "address_attempts",
                        LoginLimiter(max_failures=LOGIN_ATTEMPTS_PER_ADDRESS, lockout_seconds=LOGIN_ATTEMPT_WINDOW_SECONDS))
    monkeypatch.setattr(auth_routes, "password_change_limiter", LoginLimiter())


@pytest.fixture
def unreadable_model_lists(monkeypatch):
    async def unreadable(endpoint, api_key_fn):
        return None, connection_health.BAD_ANSWER

    monkeypatch.setattr(connection_health, "models_or_failure", unreadable)
    monkeypatch.setattr(routes, "connection_health", ConnectionHealth(lambda: routes.endpoints.list(), lambda e: None))
