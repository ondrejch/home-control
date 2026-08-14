"""Unit tests for night HVAC fan window transitions."""

import time

import automate_home


def _local_timestamp(hour: int, minute: int = 0, second: int = 0) -> float:
    now = time.localtime()
    return time.mktime(
        (
            now.tm_year,
            now.tm_mon,
            now.tm_mday,
            hour,
            minute,
            second,
            now.tm_wday,
            now.tm_yday,
            now.tm_isdst,
        )
    )


def test_night_fan_stays_off_before_window(monkeypatch):
    """Just before 1am should not start the fan."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    automate_home.process_night_fan_state(now=_local_timestamp(0, 59))

    assert calls == []
    assert automate_home.ghome["night_fan"]["active"] is False


def test_night_fan_turns_on_at_1am(monkeypatch):
    """Entering the 1am slot should start the fan for 15 minutes."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda mode, duration_seconds=None: calls.append((mode, duration_seconds)),
    )

    now = _local_timestamp(1, 0)
    automate_home.process_night_fan_state(now=now)

    assert calls == [("ON", 15 * 60)]
    assert automate_home.ghome["night_fan"]["active"] is True
    assert automate_home.ghome["night_fan"]["last_set_at"] == now


def test_night_fan_does_not_repeat_in_same_hourly_slot(monkeypatch):
    """A second tick inside the same 15-minute slot should not send another command."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda mode, duration_seconds=None: calls.append((mode, duration_seconds)),
    )

    automate_home.process_night_fan_state(now=_local_timestamp(1, 0))
    automate_home.process_night_fan_state(now=_local_timestamp(1, 10))

    assert calls == [("ON", 15 * 60)]
    assert automate_home.ghome["night_fan"]["active"] is True


def test_night_fan_restarts_at_next_hour(monkeypatch):
    """Each hour in the window should start a new 15-minute run."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda mode, duration_seconds=None: calls.append((mode, duration_seconds)),
    )

    automate_home.process_night_fan_state(now=_local_timestamp(1, 0))
    automate_home.process_night_fan_state(now=_local_timestamp(2, 0))

    assert calls == [("ON", 15 * 60), ("ON", 15 * 60)]
    assert automate_home.ghome["night_fan"]["active"] is True


def test_night_fan_turns_off_after_15_minute_slot(monkeypatch):
    """Leaving the 15-minute slot should turn the fan off while still in the window."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda mode, duration_seconds=None: calls.append((mode, duration_seconds)),
    )

    automate_home.process_night_fan_state(now=_local_timestamp(1, 0))
    automate_home.process_night_fan_state(now=_local_timestamp(1, 15))

    assert calls == [("ON", 15 * 60), ("OFF", None)]
    assert automate_home.ghome["night_fan"]["active"] is False
    assert automate_home.ghome["night_fan"]["last_set_at"] is None


def test_night_fan_stays_off_between_hourly_slots(monkeypatch):
    """Minutes 15-59 of each hour in the window should not start the fan."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    automate_home.process_night_fan_state(now=_local_timestamp(2, 20))

    assert calls == []
    assert automate_home.ghome["night_fan"]["active"] is False


def test_night_fan_turns_off_at_7am(monkeypatch):
    """Leaving the window should turn the fan off once."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda mode, duration_seconds=None: calls.append((mode, duration_seconds)),
    )

    with automate_home.lock:
        automate_home.ghome["night_fan"]["active"] = True
        automate_home.ghome["night_fan"]["last_set_at"] = _local_timestamp(6, 0)

    automate_home.process_night_fan_state(now=_local_timestamp(7, 0))

    assert calls == [("OFF", None)]
    assert automate_home.ghome["night_fan"]["active"] is False
    assert automate_home.ghome["night_fan"]["last_set_at"] is None


def test_night_fan_starts_mid_slot_with_remaining_duration(monkeypatch):
    """A mid-slot start should use remaining seconds until the 15-minute mark."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda mode, duration_seconds=None: calls.append((mode, duration_seconds)),
    )

    automate_home.process_night_fan_state(now=_local_timestamp(3, 10))

    assert calls == [("ON", 5 * 60)]
    assert automate_home.ghome["night_fan"]["active"] is True


def test_night_fan_does_not_start_when_off_grid(monkeypatch):
    """Outage should prevent starting the night fan."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with automate_home.lock:
        automate_home.ghome["powerwall"]["on_grid"] = False

    automate_home.process_night_fan_state(now=_local_timestamp(2, 0))

    assert calls == []
    assert automate_home.ghome["night_fan"]["active"] is False


def test_night_fan_does_not_start_when_thermostat_off(monkeypatch):
    """Outage HVAC-off flag should prevent starting the night fan."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    with automate_home.lock:
        automate_home.ghome["is_thermostat_off"] = True

    automate_home.process_night_fan_state(now=_local_timestamp(2, 0))

    assert calls == []
    assert automate_home.ghome["night_fan"]["active"] is False


def test_night_fan_turns_off_during_outage(monkeypatch):
    """An active night fan should stop if grid is lost."""
    calls = []
    monkeypatch.setattr(
        automate_home,
        "set_thermostat_fan",
        lambda mode, duration_seconds=None: calls.append((mode, duration_seconds)),
    )

    with automate_home.lock:
        automate_home.ghome["night_fan"]["active"] = True
        automate_home.ghome["powerwall"]["on_grid"] = False

    automate_home.process_night_fan_state(now=_local_timestamp(2, 0))

    assert calls == [("OFF", None)]
    assert automate_home.ghome["night_fan"]["active"] is False
