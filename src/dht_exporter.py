#!/usr/bin/env python3
"""Prometheus textfile exporter for DHT temperature/humidity and GMC radiation CPM.

This script is intended to run as a short-lived `systemd` oneshot service triggered
by a timer. It reads sensors once and atomically updates a `.prom` file consumed by
Node Exporter textfile collector.
"""

from __future__ import annotations

import grp
import http.client
import os
import pwd
import sys
import tempfile
import time
import urllib.parse
from typing import Optional, Sequence

import adafruit_dht
import board
import serial

# ---- configuration ----
METRIC_FILE = "/var/lib/node_exporter/textfile_collector/dht.prom"
NODE_EXPORTER_USER = "node_exporter"
NODE_EXPORTER_GROUP = "node_exporter"

SERIAL_BAUDRATE = 57600
SERIAL_OPEN_TIMEOUT_SECONDS = 1
SERIAL_READ_TIMEOUT_SECONDS = 2
GMC_COMMAND_GET_CPM = "<GETCPM>>"
RADIATION_ALERT_CPM = int(os.environ.get("RADIATION_ALERT_CPM", "100"))
RADIATION_ALERT_COOLDOWN_SECONDS = int(os.environ.get("RADIATION_ALERT_COOLDOWN_SECONDS", "1800"))

# State file stores last sent high-radiation alert timestamp.
RADIATION_ALERT_STATE_FILE = os.environ.get(
    "RADIATION_ALERT_STATE_FILE",
    "/var/lib/node_exporter/textfile_collector/.radiation_alert_ts",
)

# Comma-separated override example: GMC_PORTS="/dev/ttyUSB0,/dev/ttyUSB1"
GMC_PORT_CANDIDATES = tuple(
    p.strip() for p in os.environ.get("GMC_PORTS", "/dev/ttyUSB0").split(",") if p.strip()
)

# DHT configuration (examples: DHT_MODEL=DHT22, DHT_PIN=D4).
DHT_MODEL = os.environ.get("DHT_MODEL", "DHT22").strip().upper()
DHT_PIN = os.environ.get("DHT_PIN", "D4").strip()
# -----------------------

# Add the configuration directory to the Python path to find secrets.
sys.path.insert(0, "/etc/home-automation")

try:
    from _secrets_pushover import PUSHOVER_APP_TOKEN, PUSHOVER_USERGROUP
except ModuleNotFoundError:
    # Keep exporter functional even when push notifications are not configured.
    PUSHOVER_APP_TOKEN = ""
    PUSHOVER_USERGROUP = ""


def send_pushover(message: str) -> None:
    """Send a Pushover notification if credentials are configured.

    Args:
        message: Human-readable alert text.
    """
    if not (PUSHOVER_APP_TOKEN and PUSHOVER_USERGROUP):
        return

    try:
        conn = http.client.HTTPSConnection("api.pushover.net", 443, timeout=5)
        conn.request(
            "POST",
            "/1/messages.json",
            urllib.parse.urlencode(
                {
                    "token": PUSHOVER_APP_TOKEN,
                    "user": PUSHOVER_USERGROUP,
                    "message": message,
                }
            ),
            {"Content-type": "application/x-www-form-urlencoded"},
        )
        conn.getresponse()
        conn.close()
    except OSError:
        # Notification failures should not block metric export.
        return


def create_dht_sensor(model: str = DHT_MODEL, pin_name: str = DHT_PIN):
    """Create a DHT sensor object from model and board pin name.

    Args:
        model: DHT model string, either `DHT22` or `DHT11`.
        pin_name: `board` module pin attribute, e.g. `D4`.

    Returns:
        Instantiated adafruit DHT sensor object.

    Raises:
        ValueError: If model or pin is invalid.
    """
    pin = getattr(board, pin_name, None)
    if pin is None:
        raise ValueError(f"Unknown board pin '{pin_name}'.")
    if model == "DHT22":
        return adafruit_dht.DHT22(pin)
    if model == "DHT11":
        return adafruit_dht.DHT11(pin)
    raise ValueError(f"Unsupported DHT model '{model}'. Use DHT22 or DHT11.")


def can_open_port(port: str) -> bool:
    """Return True if a serial port can be opened.

    Args:
        port: Device path such as `/dev/ttyUSB0`.
    """
    try:
        with serial.Serial(port, SERIAL_BAUDRATE, timeout=SERIAL_OPEN_TIMEOUT_SECONDS):
            return True
    except (serial.SerialException, OSError):
        return False


def detect_port(candidates: Sequence[str] = GMC_PORT_CANDIDATES) -> Optional[str]:
    """Detect the first accessible GMC serial port.

    Args:
        candidates: Ordered list of serial device paths to probe.

    Returns:
        First reachable port path, or None if no candidate is available.
    """
    for port in candidates:
        if can_open_port(port):
            return port
    return None


def get_cpm(serial_port: serial.Serial) -> Optional[int]:
    """Read CPM value from an open GMC serial connection.

    Args:
        serial_port: Already-opened serial connection to GMC counter.

    Returns:
        Counts per minute if decode succeeds, otherwise None.
    """
    try:
        # GMC protocol request for current CPM value.
        serial_port.write(GMC_COMMAND_GET_CPM.encode("ascii"))
        raw_cpm = serial_port.readline()
        if raw_cpm:
            return int.from_bytes(raw_cpm, "big")
    except (serial.SerialException, OSError, ValueError):
        return None
    return None


def get_radiation_cpm() -> Optional[int]:
    """Read radiation CPM from GMC-300.

    Returns:
        CPM value, or None if detector is unavailable/unreadable.
    """
    try:
        port = detect_port()
        if port is None:
            return None

        with serial.Serial(port, SERIAL_BAUDRATE, timeout=SERIAL_READ_TIMEOUT_SECONDS) as serial_port:
            cpm = get_cpm(serial_port)
            if should_send_radiation_alert(cpm):
                send_pushover(f"Radiation detector {cpm} cpm")
            return cpm
    except (serial.SerialException, OSError):
        return None


def _read_last_alert_timestamp(state_file: str = RADIATION_ALERT_STATE_FILE) -> Optional[float]:
    """Read last high-radiation alert timestamp from state file."""
    try:
        with open(state_file, "r", encoding="utf-8") as file_handle:
            return float(file_handle.read().strip())
    except (OSError, ValueError):
        return None


def _write_last_alert_timestamp(timestamp: float, state_file: str = RADIATION_ALERT_STATE_FILE) -> None:
    """Persist the latest high-radiation alert timestamp atomically."""
    state_dir = os.path.dirname(state_file)
    os.makedirs(state_dir, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=state_dir, delete=False, encoding="utf-8") as file_handle:
        tmp_path = file_handle.name
        file_handle.write(f"{timestamp:.6f}\n")
    os.replace(tmp_path, state_file)


def should_send_radiation_alert(
    cpm: Optional[int],
    now: Optional[float] = None,
    *,
    threshold: int = RADIATION_ALERT_CPM,
    cooldown_seconds: int = RADIATION_ALERT_COOLDOWN_SECONDS,
    state_file: str = RADIATION_ALERT_STATE_FILE,
) -> bool:
    """Decide whether to send a high-radiation alert based on threshold and cooldown.

    Args:
        cpm: Current counts per minute.
        now: Optional UNIX timestamp used for deterministic tests.
        threshold: CPM level that triggers alerts.
        cooldown_seconds: Minimum seconds between consecutive alerts.
        state_file: File used to store last alert timestamp.

    Returns:
        True when alert should be sent; False otherwise.
    """
    if cpm is None or cpm <= threshold:
        return False
    if now is None:
        now = time.time()
    if cooldown_seconds <= 0:
        return True

    last_sent = _read_last_alert_timestamp(state_file=state_file)
    if last_sent is not None and (now - last_sent) < cooldown_seconds:
        return False

    try:
        _write_last_alert_timestamp(now, state_file=state_file)
    except OSError:
        # If state persistence fails, allow alert to avoid missing critical event.
        pass
    return True


def write_metrics(temp_c: float, hum: float, radiation_cpm: Optional[int] = None) -> None:
    """Write metrics to Node Exporter textfile collector atomically.

    Args:
        temp_c: Temperature in degrees Celsius.
        hum: Relative humidity in percent.
        radiation_cpm: Optional radiation counts-per-minute.
    """
    lines = [
        "# HELP pigate_dht_temperature_celsius Temperature from DHT sensor in Celsius",
        "# TYPE pigate_dht_temperature_celsius gauge",
        f"pigate_dht_temperature_celsius {temp_c:.2f}",
        "",
        "# HELP pigate_dht_humidity_percent Humidity from DHT sensor in percent",
        "# TYPE pigate_dht_humidity_percent gauge",
        f"pigate_dht_humidity_percent {hum:.2f}",
        "",
    ]

    if radiation_cpm is not None:
        lines.extend(
            [
                "# HELP pigate_radiation_cpm Radiation level in counts per minute from GMC-300",
                "# TYPE pigate_radiation_cpm gauge",
                f"pigate_radiation_cpm {radiation_cpm}",
                "",
            ]
        )

    metrics_text = "\n".join(lines) + "\n"
    metrics_dir = os.path.dirname(METRIC_FILE)
    os.makedirs(metrics_dir, exist_ok=True)

    # Write to temp file then move into place to avoid partial reads by node_exporter.
    with tempfile.NamedTemporaryFile("w", dir=metrics_dir, delete=False, encoding="utf-8") as file_handle:
        tmp_path = file_handle.name
        file_handle.write(metrics_text)

    os.replace(tmp_path, METRIC_FILE)

    # Assign metric ownership to node_exporter if that account exists.
    try:
        uid = pwd.getpwnam(NODE_EXPORTER_USER).pw_uid
        gid = grp.getgrnam(NODE_EXPORTER_GROUP).gr_gid
        os.chown(METRIC_FILE, uid, gid)
    except KeyError:
        pass

    # Keep output world-readable for scrapers.
    os.chmod(METRIC_FILE, 0o644)


def main() -> None:
    """Read sensors once and write metrics when DHT data is available."""
    temp_c = None
    hum = None
    radiation_cpm = None
    sensor = None

    try:
        sensor = create_dht_sensor()
        # DHT sensors occasionally return transient read errors.
        temp_c = sensor.temperature
        hum = sensor.humidity
    except (RuntimeError, ValueError, AttributeError):
        pass

    # Exporter should still publish DHT values even when GMC read fails.
    radiation_cpm = get_radiation_cpm()

    if temp_c is not None and hum is not None:
        write_metrics(temp_c, hum, radiation_cpm)

    # Explicitly release GPIO resources before process exit.
    if sensor is not None:
        try:
            sensor.exit()
        except RuntimeError:
            pass


if __name__ == "__main__":
    main()
