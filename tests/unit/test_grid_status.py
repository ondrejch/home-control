"""Unit tests for Powerwall telemetry parsing and fail-safe behavior."""

import requests

import automate_home


def test_get_grid_status_parses_alerts_and_soe(monkeypatch):
    """Expected dashboard payload should map to `(on_grid, soe)` tuple."""

    def fake_request(method, url, **kwargs):
        if url == automate_home.POWERWALL_ALERTS_URL:
            return ["SystemConnectedToGrid"]
        if url == automate_home.POWERWALL_SOE_URL:
            return {"percentage": 37.5}
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(automate_home, "_request_json", fake_request)
    on_grid, soe = automate_home.get_grid_status()
    assert on_grid is True
    assert soe == 37.5


def test_get_grid_status_clamps_soe(monkeypatch):
    """Out-of-range SoE values should be clamped to physical bounds."""

    def fake_request(method, url, **kwargs):
        if url == automate_home.POWERWALL_ALERTS_URL:
            return []
        if url == automate_home.POWERWALL_SOE_URL:
            return {"percentage": 120.0}
        raise AssertionError(f"Unexpected URL {url}")

    monkeypatch.setattr(automate_home, "_request_json", fake_request)
    on_grid, soe = automate_home.get_grid_status()
    assert on_grid is False
    assert soe == 100.0


def test_get_grid_status_keeps_fail_safe_when_dashboard_unavailable(monkeypatch):
    """Powerwall errors should keep current fail-safe `(True, 100.0)` behavior."""

    def fake_request(method, url, **kwargs):
        raise requests.RequestException("connection failed")

    monkeypatch.setattr(automate_home, "_request_json", fake_request)
    on_grid, soe = automate_home.get_grid_status()
    assert on_grid is True
    assert soe == 100.0
