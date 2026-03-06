"""Integration-style outage/recovery workflow tests using real transition logic."""

import automate_home


def test_outage_to_recovery_end_to_end(monkeypatch):
    """Outage flow should turn off HVAC, delay, and restore original mode."""
    events = []
    monkeypatch.setattr(automate_home, "send_email", lambda subject, body: events.append(("email", subject)))
    monkeypatch.setattr(automate_home, "send_pushover", lambda message: events.append(("push", message)))
    monkeypatch.setattr(automate_home, "set_thermostat_mode", lambda mode: events.append(("mode", mode)))

    with automate_home.lock:
        automate_home.ghome["thermostat"]["mode"] = "HEAT"

    automate_home.process_powerwall_state(on_grid=False, soe=40.0, now=1000.0)
    automate_home.process_powerwall_state(on_grid=False, soe=9.5, now=1010.0)
    automate_home.process_powerwall_state(on_grid=True, soe=40.0, now=1100.0)
    automate_home.process_powerwall_state(on_grid=True, soe=40.0, now=1405.0)

    assert ("mode", "OFF") in events
    assert ("email", "Power Outage Detected") in events
    assert ("push", "Grid OFF") in events
    assert ("email", "Critical Alert: Powerwall Battery Low") in events
    assert ("email", "Power Restored") in events
    assert ("push", "Grid back ON") in events
    assert ("mode", "HEAT") in events
