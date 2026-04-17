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


def test_excess_solar_override_sets_70f_before_4pm(monkeypatch):
    """High SoE before 4pm should temporarily apply the solar-use setpoint."""
    setpoint_calls = []
    monkeypatch.setattr(automate_home, "send_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "send_pushover", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "set_thermostat_mode", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "_is_before_solar_cutoff", lambda now: True)
    monkeypatch.setattr(automate_home, "_is_after_solar_cutoff", lambda now: False)
    monkeypatch.setattr(
        automate_home,
        "refresh_thermostat_status",
        lambda now=None: {
            "time": "now",
            "mode": "COOL",
            "is_eco": False,
            "ambient_temperature_celsius": 24.0,
            "cool_celsius": 24.4,
            "heat_celsius": None,
        },
    )
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_setpoint",
        lambda mode, **kwargs: setpoint_calls.append((mode, kwargs)),
    )

    automate_home.process_powerwall_state(on_grid=True, soe=96.0, now=1000.0)

    assert setpoint_calls == [("COOL", {"heat_celsius": None, "cool_celsius": 21.1})]
    assert automate_home.ghome["solar_excess_override"]["active"] is True
    assert automate_home.ghome["solar_excess_override"]["original_cool_celsius"] == 24.4
    assert automate_home.ghome["solar_excess_override"]["override_cool_celsius"] == 21.1


def test_excess_solar_override_restores_original_setpoint_after_4pm(monkeypatch):
    """An active solar override should restore the original setpoint after 4pm."""
    setpoint_calls = []
    monkeypatch.setattr(automate_home, "send_email", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "send_pushover", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "set_thermostat_mode", lambda *args, **kwargs: None)
    monkeypatch.setattr(automate_home, "_is_before_solar_cutoff", lambda now: False)
    monkeypatch.setattr(automate_home, "_is_after_solar_cutoff", lambda now: True)
    monkeypatch.setattr(
        automate_home,
        "refresh_thermostat_status",
        lambda now=None: {
            "time": "now",
            "mode": "HEAT",
            "is_eco": False,
            "ambient_temperature_celsius": 20.0,
            "cool_celsius": None,
            "heat_celsius": 21.1,
        },
    )
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_setpoint",
        lambda mode, **kwargs: setpoint_calls.append((mode, kwargs)),
    )

    with automate_home.lock:
        automate_home.ghome["solar_excess_override"].update(
            {
                "active": True,
                "triggered_at": 900.0,
                "original_mode": "HEAT",
                "original_heat_celsius": 19.0,
                "original_cool_celsius": None,
                "override_heat_celsius": 21.1,
                "override_cool_celsius": None,
            }
        )

    automate_home.process_powerwall_state(on_grid=True, soe=80.0, now=1700.0)

    assert setpoint_calls == [("HEAT", {"heat_celsius": 19.0, "cool_celsius": None})]
    assert automate_home.ghome["solar_excess_override"]["active"] is False
