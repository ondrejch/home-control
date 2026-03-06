"""Unit tests for outage/recovery control state transitions."""

import automate_home


def test_outage_turns_off_thermostat_and_notifies(monkeypatch):
    """Grid loss transition should notify and turn HVAC OFF once."""
    calls = {"mode": [], "email": [], "push": []}

    monkeypatch.setattr(automate_home, "send_email", lambda subject, body: calls["email"].append(subject))
    monkeypatch.setattr(automate_home, "send_pushover", lambda message: calls["push"].append(message))
    monkeypatch.setattr(automate_home, "set_thermostat_mode", lambda mode: calls["mode"].append(mode))

    with automate_home.lock:
        automate_home.ghome["thermostat"]["mode"] = "HEAT"

    automate_home.process_powerwall_state(on_grid=False, soe=50.0, now=1000.0)

    assert calls["mode"] == ["OFF"]
    assert calls["email"] == ["Power Outage Detected"]
    assert calls["push"] == ["Grid OFF"]
    assert automate_home.ghome["is_thermostat_off"] is True
    assert automate_home.ghome["last_recovered_power"] is None


def test_recovery_waits_before_restoring_mode(monkeypatch):
    """HVAC restore should happen only after the configured delay."""
    calls = {"mode": []}
    monkeypatch.setattr(automate_home, "send_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "send_pushover", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "set_thermostat_mode", lambda mode: calls["mode"].append(mode))

    with automate_home.lock:
        automate_home.ghome["powerwall"]["on_grid"] = False
        automate_home.ghome["is_thermostat_off"] = True
        automate_home.ghome["thermostat"]["mode"] = "COOL"

    automate_home.process_powerwall_state(on_grid=True, soe=60.0, now=2000.0)
    assert calls["mode"] == []
    assert automate_home.ghome["last_recovered_power"] == 2000.0

    automate_home.process_powerwall_state(on_grid=True, soe=61.0, now=2301.0)
    assert calls["mode"] == ["COOL"]
    assert automate_home.ghome["is_thermostat_off"] is False
    assert automate_home.ghome["last_recovered_power"] is None


def test_low_battery_notified_once_and_resets_on_recovery(monkeypatch):
    """Low battery alert should fire once per outage and reset after recovery."""
    sent_subjects = []
    monkeypatch.setattr(automate_home, "send_email", lambda subject, body: sent_subjects.append(subject))
    monkeypatch.setattr(automate_home, "send_pushover", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "set_thermostat_mode", lambda *args, **kwargs: None)

    automate_home.process_powerwall_state(on_grid=False, soe=9.99, now=100.0)
    automate_home.process_powerwall_state(on_grid=False, soe=5.0, now=105.0)
    assert sent_subjects.count("Critical Alert: Powerwall Battery Low") == 1
    assert automate_home.ghome["low_battery_notified"] is True

    automate_home.process_powerwall_state(on_grid=True, soe=50.0, now=110.0)
    assert automate_home.ghome["low_battery_notified"] is False


def test_low_battery_threshold_is_strictly_less_than_10(monkeypatch):
    """SoE at exactly 10.0 should not trigger critical low battery alert."""
    sent_subjects = []
    monkeypatch.setattr(automate_home, "send_email", lambda subject, body: sent_subjects.append(subject))
    monkeypatch.setattr(automate_home, "send_pushover", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "set_thermostat_mode", lambda *args, **kwargs: None)

    automate_home.process_powerwall_state(on_grid=False, soe=10.0, now=100.0)
    assert "Critical Alert: Powerwall Battery Low" not in sent_subjects
