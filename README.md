# Home Automation Power Management

This service monitors Tesla Powerwall grid status and automatically controls a Google Nest thermostat during outages to reduce battery drain.

## Features

- Powerwall monitoring from local `Powerwall-Dashboard` endpoints.
- Automatic HVAC shutdown when grid disconnect is detected.
- Delayed HVAC restore (5 minutes) after grid recovery.
- Low battery alerting when state-of-energy drops below 10%.
- Email notifications (Gmail app password).
- Optional Pushover notifications (`PUSHOVER_ENABLED=True` by default).
- `systemd` service deployment for unattended operation.
- Optional DHT22 + GMC-300 exporter for Prometheus textfile collector.

## Prerequisites

- Linux host with `systemd`.
- Python 3.8+.
- Running [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard) on `http://localhost:8675`.
- Google Smart Device Management credentials.
- Gmail account with app password for email alerts.
- (Optional exporter) Node Exporter with textfile collector path:
  `/var/lib/node_exporter/textfile_collector`.

## Installation

1. Clone:
   ```bash
   git clone <your-repository-url>
   cd <repository-directory>
   ```
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Configure secrets:
   ```bash
   sudo mkdir -p /etc/home-automation
   sudo cp src/_secrets.py.template /etc/home-automation/_secrets.py
   sudo nano /etc/home-automation/_secrets.py
   ```
4. Install/start service:
   ```bash
   sudo ./setup.sh
   ```

## Optional DHT/GMC Exporter

The repository includes:

- `src/dht_exporter.py` (reads DHT22 and optional GMC-300 radiation CPM)
- `etc/dht_exporter.service`
- `etc/dht_exporter.timer` (runs every 30 seconds, with `Persistent=true`)

### Exporter dependencies

Install Python packages for the exporter:

```bash
pip install adafruit-circuitpython-dht adafruit-blinka pyserial
```

On Raspberry Pi, you may also need:

```bash
sudo apt-get install -y libgpiod2
```

### Exporter installation

```bash
sudo cp src/dht_exporter.py /usr/local/bin/dht_exporter.py
sudo chmod +x /usr/local/bin/dht_exporter.py
sudo cp etc/dht_exporter.service /etc/systemd/system/dht_exporter.service
sudo cp etc/dht_exporter.timer /etc/systemd/system/dht_exporter.timer
sudo systemctl daemon-reload
sudo systemctl enable --now dht_exporter.timer
```

The service runs as `node_exporter` user (`SupplementaryGroups=dialout gpio` for USB serial and GPIO access).

### Exporter status/logs

```bash
sudo systemctl status dht_exporter.timer
sudo systemctl status dht_exporter.service
sudo journalctl -u dht_exporter.service -f
```

The exporter writes metrics to:
`/var/lib/node_exporter/textfile_collector/dht.prom`

For Pushover alerts from `dht_exporter.py`, provide:
`/etc/home-automation/_secrets_pushover.py` with `PUSHOVER_APP_TOKEN` and
`PUSHOVER_USERGROUP`.

### Exporter configuration via environment

Supported environment variables:

- `DHT_MODEL` (`DHT22` default, `DHT11` supported)
- `DHT_PIN` (`D4` default)
- `GMC_PORTS` (comma-separated serial ports; default `/dev/ttyUSB0`)
- `RADIATION_ALERT_CPM` (default `100`)
- `RADIATION_ALERT_COOLDOWN_SECONDS` (default `1800`)
- `RADIATION_ALERT_STATE_FILE` (default `/var/lib/node_exporter/textfile_collector/.radiation_alert_ts`)

Example `systemd` override:

```bash
sudo systemctl edit dht_exporter.service
```

Add:

```ini
[Service]
Environment="DHT_MODEL=DHT22"
Environment="DHT_PIN=D4"
Environment="GMC_PORTS=/dev/ttyUSB0,/dev/ttyUSB1"
Environment="RADIATION_ALERT_CPM=100"
Environment="RADIATION_ALERT_COOLDOWN_SECONDS=3600"
```

## Secrets Notes

- `PROJECT_ID` accepts either `<ENTERPRISE_ID>` or `enterprises/<ENTERPRISE_ID>`.
- `DEVICE_ID` accepts either `<DEVICE_ID>` or full SDM resource path.
- `PUSHOVER_ENABLED` is optional and defaults to enabled if omitted.

## Example Powerwall Inputs

- `GET http://localhost:8675/alerts` expected shape:
  ```json
  ["SystemConnectedToGrid"]
  ```
- `GET http://localhost:8675/soe` expected shape:
  ```json
  {"percentage": 62.4}
  ```

## Running Tests

```bash
pytest -q
```

## Service Management

- Status:
  ```bash
  sudo systemctl status home-automation.service
  ```
- Live logs:
  ```bash
  sudo journalctl -u home-automation.service -f
  ```
- Restart:
  ```bash
  sudo systemctl restart home-automation.service
  ```

Optional file logging is available in code (`log_to_file=True`) and writes to:
`/var/log/home-automation/home-automation.log`.


## Useful Resources

- [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard)
- [pyPowerwall](https://github.com/jasonacox/pypowerwall)
- [Google Nest SDM Python library](https://github.com/allenporter/python-google-nest-sdm)

## Author

Ondrej Chvala (<ochvala@gmail.com>)
