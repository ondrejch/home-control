"""Pytest configuration for home-control tests."""

import sys
import types
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

if "_secrets" not in sys.modules:
    fake_secrets = types.ModuleType("_secrets")
    fake_secrets.CLIENT_ID = "client-id"
    fake_secrets.CLIENT_SECRET = "client-secret"
    fake_secrets.REFRESH_TOKEN = "refresh-token"
    fake_secrets.PROJECT_ID = "123456789"
    fake_secrets.DEVICE_ID = "device-123"
    fake_secrets.GMAIL_ADDRESS = "sender@example.com"
    fake_secrets.GMAIL_PASSWORD = "password"
    fake_secrets.NOTIFY_EMAILS = ["notify@example.com"]
    fake_secrets.PUSHOVER_APP_TOKEN = "pushover-token"
    fake_secrets.PUSHOVER_USERGROUP = "pushover-user"
    fake_secrets.PUSHOVER_ENABLED = True
    sys.modules["_secrets"] = fake_secrets


@pytest.fixture(autouse=True)
def reset_shared_state():
    """Reset module state so tests remain isolated and deterministic."""
    import automate_home

    with automate_home.lock:
        automate_home.ghome.clear()
        automate_home.ghome.update(automate_home._initial_state())
    yield
