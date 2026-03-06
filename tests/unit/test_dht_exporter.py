"""Unit tests for DHT/GMC exporter helpers."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


@pytest.fixture
def exporter_module(monkeypatch):
    """Load `dht_exporter.py` with stubbed hardware modules."""
    board_module = types.ModuleType("board")
    board_module.D4 = "PIN_D4"
    board_module.D17 = "PIN_D17"

    adafruit_dht_module = types.ModuleType("adafruit_dht")

    class _FakeSensor:
        def __init__(self, pin):
            self.pin = pin
            self.temperature = 23.4
            self.humidity = 45.6
            self.exited = False

        def exit(self):
            self.exited = True

    class DHT22(_FakeSensor):
        """Fake DHT22 sensor."""

    class DHT11(_FakeSensor):
        """Fake DHT11 sensor."""

    adafruit_dht_module.DHT22 = DHT22
    adafruit_dht_module.DHT11 = DHT11

    serial_module = types.ModuleType("serial")

    class SerialException(Exception):
        """Fake serial exception."""

    class Serial:
        """Fake serial port object."""

        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self._written = b""

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

        def write(self, data):
            self._written = data

        def readline(self):
            return b""

    serial_module.SerialException = SerialException
    serial_module.Serial = Serial

    pushover_secrets = types.ModuleType("_secrets_pushover")
    pushover_secrets.PUSHOVER_APP_TOKEN = "token"
    pushover_secrets.PUSHOVER_USERGROUP = "user"

    monkeypatch.setitem(sys.modules, "board", board_module)
    monkeypatch.setitem(sys.modules, "adafruit_dht", adafruit_dht_module)
    monkeypatch.setitem(sys.modules, "serial", serial_module)
    monkeypatch.setitem(sys.modules, "_secrets_pushover", pushover_secrets)

    module_path = Path(__file__).resolve().parents[2] / "src" / "dht_exporter.py"
    spec = importlib.util.spec_from_file_location("dht_exporter_testable", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_create_dht_sensor_uses_model_and_pin(exporter_module):
    """Configured model/pin should map to expected sensor class."""
    sensor = exporter_module.create_dht_sensor(model="DHT11", pin_name="D17")
    assert sensor.__class__.__name__ == "DHT11"
    assert sensor.pin == "PIN_D17"


def test_detect_port_returns_first_accessible_candidate(exporter_module, monkeypatch):
    """Port detector should return the first candidate that can open."""
    monkeypatch.setattr(
        exporter_module,
        "can_open_port",
        lambda port: port in ("/dev/ttyUSB1", "/dev/ttyUSB2"),
    )
    assert exporter_module.detect_port(["/dev/ttyUSB0", "/dev/ttyUSB1", "/dev/ttyUSB2"]) == "/dev/ttyUSB1"


def test_get_cpm_writes_command_and_decodes_big_endian(exporter_module):
    """CPM should decode from binary payload and use GMC request command."""

    class FakeSerialPort:
        def __init__(self):
            self.written = b""

        def write(self, data):
            self.written = data

        def readline(self):
            return b"\x00\x64"

    fake_port = FakeSerialPort()
    assert exporter_module.get_cpm(fake_port) == 100
    assert fake_port.written == b"<GETCPM>>"


def test_should_send_radiation_alert_respects_cooldown(exporter_module, tmp_path):
    """Alerts should be throttled by cooldown state file."""
    state_file = tmp_path / "alert_state.txt"

    assert exporter_module.should_send_radiation_alert(
        150,
        now=1000.0,
        threshold=100,
        cooldown_seconds=60,
        state_file=str(state_file),
    )
    assert not exporter_module.should_send_radiation_alert(
        150,
        now=1020.0,
        threshold=100,
        cooldown_seconds=60,
        state_file=str(state_file),
    )
    assert exporter_module.should_send_radiation_alert(
        150,
        now=1061.0,
        threshold=100,
        cooldown_seconds=60,
        state_file=str(state_file),
    )


def test_write_metrics_outputs_expected_prometheus_text(exporter_module, tmp_path, monkeypatch):
    """Metric writer should emit DHT and optional radiation metrics."""
    metric_file = tmp_path / "dht.prom"
    monkeypatch.setattr(exporter_module, "METRIC_FILE", str(metric_file))
    monkeypatch.setattr(exporter_module, "NODE_EXPORTER_USER", "does-not-exist-user")
    monkeypatch.setattr(exporter_module, "NODE_EXPORTER_GROUP", "does-not-exist-group")

    exporter_module.write_metrics(21.25, 42.5, radiation_cpm=123)
    content = metric_file.read_text(encoding="utf-8")

    assert "pigate_dht_temperature_celsius 21.25" in content
    assert "pigate_dht_humidity_percent 42.50" in content
    assert "pigate_radiation_cpm 123" in content


def test_get_radiation_cpm_sends_pushover_when_threshold_exceeded(exporter_module, monkeypatch):
    """High CPM values should trigger one push notification when allowed."""
    monkeypatch.setattr(exporter_module, "detect_port", lambda: "/dev/ttyUSB0")

    class FakeSerial:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    monkeypatch.setattr(exporter_module.serial, "Serial", FakeSerial)
    monkeypatch.setattr(exporter_module, "get_cpm", lambda _: 150)
    monkeypatch.setattr(exporter_module, "should_send_radiation_alert", lambda cpm: True)

    sent_messages = []
    monkeypatch.setattr(exporter_module, "send_pushover", lambda message: sent_messages.append(message))

    assert exporter_module.get_radiation_cpm() == 150
    assert sent_messages == ["Radiation detector 150 cpm"]
