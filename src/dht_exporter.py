#!/usr/bin/env python3
import time
import os
import tempfile
import pwd
import grp
import glob

import board
import adafruit_dht
import serial

import http.client, urllib
import sys


# ---- config ----
METRIC_FILE = "/var/lib/node_exporter/textfile_collector/dht.prom"
NODE_EXPORTER_USER = "node_exporter"
NODE_EXPORTER_GROUP = "node_exporter"

# DHT22 on GPIO4 (board.D4). Change to DHT11 if needed.
dht = adafruit_dht.DHT22(board.D4)
# dht = adafruit_dht.DHT11(board.D4)
# ---------------


# Add the configuration directory to the Python path to find _secrets.py.
sys.path.insert(0, '/etc/home-automation')
# Import secrets, including the new email list.
from _secrets_pushover import (
    PUSHOVER_APP_TOKEN, PUSHOVER_USERGROUP
)


def send_pushover(message: str):
    """ Sends a Pushover message, https://pushover.net """
    if not (PUSHOVER_APP_TOKEN and PUSHOVER_USERGROUP):
        return 
    conn = http.client.HTTPSConnection("api.pushover.net:443")
    conn.request("POST", "/1/messages.json",
                 urllib.parse.urlencode({ "token": PUSHOVER_APP_TOKEN,
                                         "user": PUSHOVER_USERGROUP,
                                         "message": message }),
                 { "Content-type": "application/x-www-form-urlencoded" }
                 )
    conn.getresponse()


# GMC-300 functions
def can_open_port(port):
    try:
        with serial.Serial(port, 57600, timeout=1):
            pass
        return True
    except:
        return False


def detect_port():
    # ports = glob.glob('/dev/*usb*') + glob.glob('/dev/*USB*')
    ports = ['/dev/ttyUSB0']
    for port in ports:
        if can_open_port(port):
            return port
    return None


def get_cpm(sr):
    try:
        sr.write("<GETCPM>>".encode())
        cpm = sr.readline()
        if len(cpm) > 0:
            return int.from_bytes(cpm, 'big')
    except:
        pass
    return None


def get_radiation_cpm():
    """Read CPM from GMC-300 detector. Returns None if device not available."""
    try:
        port = detect_port()
        if port is None:
            return None

        with serial.Serial(port, 57600, timeout=2) as s:
            cpm = get_cpm(s)
            if float(cpm) > 100:
                send_pushover(f'Radiation detecter {cpm} cpm')
            return cpm
    except Exception:
        return None


def write_metrics(temp_c, hum, radiation_cpm=None):
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

    # Add radiation metrics if available
    if radiation_cpm is not None:
        lines.extend([
            "# HELP pigate_radiation_cpm Radiation level in counts per minute from GMC-300",
            "# TYPE pigate_radiation_cpm gauge",
            f"pigate_radiation_cpm {radiation_cpm}",
            "",
        ])

    text = "\n".join(lines) + "\n"

    d = os.path.dirname(METRIC_FILE)
    with tempfile.NamedTemporaryFile("w", dir=d, delete=False) as f:
        tmpname = f.name
        f.write(text)

    os.replace(tmpname, METRIC_FILE)

    # fix owner → node_exporter, if that user/group exist
    try:
        uid = pwd.getpwnam(NODE_EXPORTER_USER).pw_uid
        gid = grp.getgrnam(NODE_EXPORTER_GROUP).gr_gid
        os.chown(METRIC_FILE, uid, gid)
    except KeyError:
        # if node_exporter user/group don't exist, just skip chown
        pass

    # ensure world-readable
    os.chmod(METRIC_FILE, 0o644)


def main():
    temp_c = None
    hum = None
    radiation_cpm = None

    # Read DHT sensor
    try:
        temp_c = dht.temperature
        hum = dht.humidity
    except RuntimeError:
        # DHT read failures are normal; just skip this cycle
        pass

    # Read radiation detector
    try:
        radiation_cpm = get_radiation_cpm()
    except Exception:
        # If radiation detector fails, we'll still export DHT data
        pass

    # Only write metrics if we have at least DHT data
    if temp_c is not None and hum is not None:
        write_metrics(temp_c, hum, radiation_cpm)


if __name__ == "__main__":
    main()

