"""Unit tests for SDM endpoint and mode control helpers."""

import pytest

import automate_home


def test_enterprise_name_from_raw_project_id(monkeypatch):
    """Raw project values should be normalized to enterprises/<id>."""
    monkeypatch.setattr(automate_home, "PROJECT_ID", "987654321")
    assert automate_home._enterprise_name() == "enterprises/987654321"


def test_enterprise_name_keeps_prefixed_value(monkeypatch):
    """Already prefixed enterprise values should be preserved."""
    monkeypatch.setattr(automate_home, "PROJECT_ID", "enterprises/abc-123")
    assert automate_home._enterprise_name() == "enterprises/abc-123"


def test_set_thermostat_mode_uses_normalized_resource(monkeypatch):
    """Mode command should target SDM executeCommand endpoint with normalized path."""
    monkeypatch.setattr(automate_home, "PROJECT_ID", "123456789")
    monkeypatch.setattr(automate_home, "DEVICE_ID", "device-123")
    monkeypatch.setattr(automate_home, "get_access_token", lambda: "token")

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = kwargs["json_payload"]
        return {}

    monkeypatch.setattr(automate_home, "_request_json", fake_request)
    automate_home.set_thermostat_mode("HEAT")

    assert captured["method"] == "POST"
    assert (
        captured["url"]
        == "https://smartdevicemanagement.googleapis.com/v1/enterprises/123456789/devices/device-123:executeCommand"
    )
    assert captured["payload"]["params"]["mode"] == "HEAT"


def test_set_thermostat_mode_rejects_invalid_mode():
    """Invalid HVAC mode values should raise ValueError explicitly."""
    with pytest.raises(ValueError, match="Invalid hvac_mode"):
        automate_home.set_thermostat_mode("BADMODE")


def test_set_thermostat_setpoint_uses_heat_command(monkeypatch):
    """Heat setpoint changes should target the SDM SetHeat command."""
    monkeypatch.setattr(automate_home, "PROJECT_ID", "123456789")
    monkeypatch.setattr(automate_home, "DEVICE_ID", "device-123")
    monkeypatch.setattr(automate_home, "get_access_token", lambda: "token")

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = kwargs["json_payload"]
        return {}

    monkeypatch.setattr(automate_home, "_request_json", fake_request)
    automate_home.set_thermostat_setpoint("HEAT", heat_celsius=21.1)

    assert captured["method"] == "POST"
    assert (
        captured["url"]
        == "https://smartdevicemanagement.googleapis.com/v1/enterprises/123456789/devices/device-123:executeCommand"
    )
    assert captured["payload"] == {
        "command": "sdm.devices.commands.ThermostatTemperatureSetpoint.SetHeat",
        "params": {"heatCelsius": 21.1},
    }


def test_set_thermostat_setpoint_rejects_invalid_range():
    """HEATCOOL range commands should require cool > heat."""
    with pytest.raises(ValueError, match="cool_celsius to be greater"):
        automate_home.set_thermostat_setpoint("HEATCOOL", heat_celsius=21.0, cool_celsius=21.0)


def test_set_thermostat_fan_on_includes_duration(monkeypatch):
    """Fan ON should send SetTimer with ISO-8601 duration."""
    monkeypatch.setattr(automate_home, "PROJECT_ID", "123456789")
    monkeypatch.setattr(automate_home, "DEVICE_ID", "device-123")
    monkeypatch.setattr(automate_home, "get_access_token", lambda: "token")

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["payload"] = kwargs["json_payload"]
        return {}

    monkeypatch.setattr(automate_home, "_request_json", fake_request)
    automate_home.set_thermostat_fan("ON", duration_seconds=14400)

    assert captured["method"] == "POST"
    assert (
        captured["url"]
        == "https://smartdevicemanagement.googleapis.com/v1/enterprises/123456789/devices/device-123:executeCommand"
    )
    assert captured["payload"] == {
        "command": "sdm.devices.commands.ThermostatFan.SetTimer",
        "params": {"timerMode": "ON", "duration": "14400s"},
    }


def test_set_thermostat_fan_off_omits_duration(monkeypatch):
    """Fan OFF should send SetTimer without a duration."""
    monkeypatch.setattr(automate_home, "PROJECT_ID", "123456789")
    monkeypatch.setattr(automate_home, "DEVICE_ID", "device-123")
    monkeypatch.setattr(automate_home, "get_access_token", lambda: "token")

    captured = {}

    def fake_request(method, url, **kwargs):
        captured["payload"] = kwargs["json_payload"]
        return {}

    monkeypatch.setattr(automate_home, "_request_json", fake_request)
    automate_home.set_thermostat_fan("OFF")

    assert captured["payload"] == {
        "command": "sdm.devices.commands.ThermostatFan.SetTimer",
        "params": {"timerMode": "OFF"},
    }


def test_set_thermostat_fan_rejects_invalid_mode():
    """Invalid fan timer mode values should raise ValueError explicitly."""
    with pytest.raises(ValueError, match="Invalid timer_mode"):
        automate_home.set_thermostat_fan("BADMODE")

