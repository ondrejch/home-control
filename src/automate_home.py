"""Power outage automation for Tesla Powerwall and Google Nest thermostat.

This module monitors a local Powerwall dashboard and updates Google SDM thermostat
state to conserve battery energy during outages.
"""

from __future__ import annotations

import logging
import os
import smtplib
import sys
import threading
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional, Tuple

import requests

# API endpoints and operational constants.
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_SDM_BASE_URL = "https://smartdevicemanagement.googleapis.com/v1"
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
POWERWALL_ALERTS_URL = "http://localhost:8675/alerts"
POWERWALL_SOE_URL = "http://localhost:8675/soe"
REQUEST_TIMEOUT_SECONDS = 10
POWERWALL_TIMEOUT_SECONDS = 5
POWERWALL_POLL_SECONDS = 5
THERMOSTAT_POLL_SECONDS = 3600
POWER_RECOVERY_DELAY_SECONDS = 300
LOW_BATTERY_THRESHOLD_PERCENT = 10.0
VALID_HVAC_MODES = {"HEAT", "COOL", "HEATCOOL", "OFF"}


def _configure_logging(log_to_file: bool = False) -> None:
    """Configure application logging once.

    Args:
        log_to_file: If True, log to `/var/log/home-automation/home-automation.log`
            in addition to stdout/journald.
    """
    root_logger = logging.getLogger()
    if root_logger.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root_logger.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    if log_to_file:
        file_handler = logging.FileHandler("/var/log/home-automation/home-automation.log")
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


_configure_logging(log_to_file=False)

# Add the configuration directory to the Python path to find `_secrets.py`.
CONFIG_DIR = os.environ.get("HOME_AUTOMATION_CONFIG_DIR", "/etc/home-automation")
if CONFIG_DIR not in sys.path:
    sys.path.insert(0, CONFIG_DIR)

try:
    import _secrets as secrets
except ModuleNotFoundError as exc:
    raise RuntimeError(f"Could not import _secrets.py from '{CONFIG_DIR}'.") from exc

# Mandatory settings.
CLIENT_ID = secrets.CLIENT_ID
CLIENT_SECRET = secrets.CLIENT_SECRET
REFRESH_TOKEN = secrets.REFRESH_TOKEN
PROJECT_ID = secrets.PROJECT_ID
DEVICE_ID = secrets.DEVICE_ID
GMAIL_ADDRESS = secrets.GMAIL_ADDRESS
GMAIL_PASSWORD = secrets.GMAIL_PASSWORD

# Optional settings.
NOTIFY_EMAILS = list(getattr(secrets, "NOTIFY_EMAILS", []))
PUSHOVER_APP_TOKEN = getattr(secrets, "PUSHOVER_APP_TOKEN", "")
PUSHOVER_USERGROUP = getattr(secrets, "PUSHOVER_USERGROUP", "")
PUSHOVER_ENABLED = bool(getattr(secrets, "PUSHOVER_ENABLED", True))

# Reuse one session for more efficient HTTP connections.
HTTP_SESSION = requests.Session()


def _initial_state() -> Dict[str, Any]:
    """Create a fresh shared state object.

    Returns:
        A dictionary containing thermostat and powerwall tracking fields.
    """
    return {
        "thermostat": {
            "time": None,
            "mode": None,
            "is_eco": None,
            "ambient_temperature_celsius": None,
            "cool_celsius": None,
            "heat_celsius": None,
        },
        "is_thermostat_off": False,
        "low_battery_notified": False,
        "last_recovered_power": None,
        "powerwall": {
            "time": None,
            # Keep current fail-safe behavior: assume on-grid at startup.
            "on_grid": True,
            "soe": None,
        },
    }


# Shared data object for thermostat and Powerwall status.
ghome = _initial_state()

# Lock for thread-safe access to the shared `ghome` object.
lock = threading.Lock()


def _enterprise_name() -> str:
    """Return an SDM enterprise path from configured project value.

    Google SDM expects `enterprises/<id>`. This keeps compatibility with either
    `PROJECT_ID=<id>` or `PROJECT_ID=enterprises/<id>`.

    Returns:
        Enterprise path for SDM URLs.
    """
    project = str(PROJECT_ID).strip().strip("/")
    if project.startswith("enterprises/"):
        return project
    return f"enterprises/{project}"


def _device_name() -> str:
    """Return full SDM device resource path.

    Returns:
        Device resource in the form `enterprises/<id>/devices/<device-id>`.
    """
    device = str(DEVICE_ID).strip().strip("/")
    if device.startswith("enterprises/"):
        return device
    if device.startswith("devices/"):
        return f"{_enterprise_name()}/{device}"
    if "/devices/" in device:
        return device
    return f"{_enterprise_name()}/devices/{device}"


def _request_json(
    method: str,
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    data: Optional[Dict[str, Any]] = None,
    json_payload: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT_SECONDS,
) -> Dict[str, Any]:
    """Send an HTTP request and decode the response JSON.

    Args:
        method: HTTP method such as `GET` or `POST`.
        url: Target URL.
        headers: Optional HTTP headers.
        data: Optional form payload.
        json_payload: Optional JSON body.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON dictionary. Returns an empty dictionary if response body is empty.

    Raises:
        requests.HTTPError: If non-success status code is returned.
        requests.RequestException: If connection-level errors occur.
        ValueError: If response content is not valid JSON.
    """
    response = HTTP_SESSION.request(
        method=method,
        url=url,
        headers=headers,
        data=data,
        json=json_payload,
        timeout=timeout,
    )
    response.raise_for_status()
    if not response.content:
        return {}
    return response.json()


def send_pushover(message: str) -> None:
    """Send a Pushover notification.

    Args:
        message: Message body.
    """
    if not PUSHOVER_ENABLED:
        return
    if not PUSHOVER_APP_TOKEN or not PUSHOVER_USERGROUP:
        logging.debug("Pushover is enabled but credentials are not configured.")
        return

    try:
        _request_json(
            "POST",
            PUSHOVER_URL,
            data={
                "token": PUSHOVER_APP_TOKEN,
                "user": PUSHOVER_USERGROUP,
                "message": message,
            },
        )
    except (requests.RequestException, ValueError) as exc:
        logging.error("Failed to send Pushover message: %s", exc)


def send_email(subject: str, body: str) -> None:
    """Send an email notification through Gmail SMTP.

    Args:
        subject: Email subject.
        body: Plain-text email body.
    """
    if not all([GMAIL_ADDRESS, GMAIL_PASSWORD, NOTIFY_EMAILS]):
        logging.warning("Email credentials or recipients are not configured. Skipping email.")
        return

    try:
        message = MIMEMultipart()
        message["From"] = GMAIL_ADDRESS
        message["To"] = ", ".join(NOTIFY_EMAILS)
        message["Subject"] = subject
        message.attach(MIMEText(body, "plain"))

        with smtplib.SMTP("smtp.gmail.com", 587, timeout=REQUEST_TIMEOUT_SECONDS) as server:
            server.starttls()
            server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
            server.sendmail(GMAIL_ADDRESS, NOTIFY_EMAILS, message.as_string())
        logging.info("Email notification sent.")
    except Exception as exc:
        logging.error("Failed to send email: %s", exc)


def get_access_token() -> str:
    """Request a Google OAuth access token.

    Returns:
        OAuth access token string.

    Raises:
        RuntimeError: If token is missing in response.
        requests.RequestException: If network/API call fails.
        ValueError: If response JSON is malformed.
    """
    token_response = _request_json(
        "POST",
        GOOGLE_TOKEN_URL,
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
    )
    access_token = token_response.get("access_token")
    if not access_token:
        raise RuntimeError("Google token response does not contain 'access_token'.")
    return access_token


def _sdm_headers(access_token: str) -> Dict[str, str]:
    """Build authorized headers for SDM requests."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def set_thermostat_mode(hvac_mode: str = "OFF") -> None:
    """Set thermostat HVAC mode.

    Args:
        hvac_mode: One of `HEAT`, `COOL`, `HEATCOOL`, `OFF`.

    Raises:
        ValueError: If mode is invalid.
        requests.RequestException: If SDM API request fails.
    """
    if hvac_mode not in VALID_HVAC_MODES:
        raise ValueError(f"Invalid hvac_mode '{hvac_mode}', expected one of {sorted(VALID_HVAC_MODES)}")

    access_token = get_access_token()
    _request_json(
        "POST",
        f"{GOOGLE_SDM_BASE_URL}/{_device_name()}:executeCommand",
        headers=_sdm_headers(access_token),
        json_payload={
            "command": "sdm.devices.commands.ThermostatMode.SetMode",
            "params": {"mode": hvac_mode},
        },
    )


def set_thermostat_eco(eco_on: bool = False) -> None:
    """Set thermostat ECO mode.

    Args:
        eco_on: True to enable `MANUAL_ECO`, False to disable ECO.
    """
    mode = "MANUAL_ECO" if eco_on else "OFF"
    access_token = get_access_token()
    _request_json(
        "POST",
        f"{GOOGLE_SDM_BASE_URL}/{_device_name()}:executeCommand",
        headers=_sdm_headers(access_token),
        json_payload={
            "command": "sdm.devices.commands.ThermostatEco.SetMode",
            "params": {"mode": mode},
        },
    )


def set_thermostat_ECO(eco_on: bool = False) -> None:
    """Backward-compatible alias for `set_thermostat_eco`."""
    set_thermostat_eco(eco_on=eco_on)


def get_thermostat_status() -> Dict[str, Any]:
    """Retrieve current thermostat status from Google SDM.

    Returns:
        JSON payload from SDM devices list endpoint.
    """
    access_token = get_access_token()
    return _request_json(
        "GET",
        f"{GOOGLE_SDM_BASE_URL}/{_enterprise_name()}/devices",
        headers=_sdm_headers(access_token),
    )


def get_grid_status() -> Tuple[bool, float]:
    """Check local Powerwall dashboard for grid status and state-of-energy.

    Returns:
        Tuple `(on_grid, state_of_energy_percent)`.
        If dashboard calls fail, returns `(True, 100.0)` to keep current fail-safe
        behavior and avoid disabling HVAC on telemetry failures.
    """
    try:
        alerts_payload = _request_json(
            "GET",
            POWERWALL_ALERTS_URL,
            timeout=POWERWALL_TIMEOUT_SECONDS,
        )
        soe_payload = _request_json(
            "GET",
            POWERWALL_SOE_URL,
            timeout=POWERWALL_TIMEOUT_SECONDS,
        )

        if isinstance(alerts_payload, list):
            on_grid = "SystemConnectedToGrid" in alerts_payload
        elif isinstance(alerts_payload, dict):
            on_grid = "SystemConnectedToGrid" in alerts_payload.keys()
        else:
            raise ValueError("Unexpected alerts payload type from Powerwall dashboard.")

        soe = float(soe_payload["percentage"])
        # Clamp into physical range so downstream threshold logic is stable.
        soe = max(0.0, min(100.0, soe))
        return on_grid, soe
    except (KeyError, TypeError, ValueError, requests.RequestException) as exc:
        logging.error("Could not read Powerwall dashboard status: %s", exc)
        return True, 100.0


def process_powerwall_state(on_grid: bool, soe: float, now: Optional[float] = None) -> None:
    """Process one powerwall status sample and apply outage/recovery actions.

    Args:
        on_grid: Current grid connectivity.
        soe: Powerwall state-of-energy percentage in `[0, 100]`.
        now: Optional UNIX timestamp used for deterministic tests.
    """
    if now is None:
        now = time.time()

    with lock:
        previous_on_grid = ghome["powerwall"]["on_grid"]
        is_thermostat_off = ghome["is_thermostat_off"]
        last_recovered_power = ghome["last_recovered_power"]
        original_thermostat_mode = ghome["thermostat"]["mode"]
        low_battery_notified = ghome["low_battery_notified"]

        ghome["powerwall"]["on_grid"] = on_grid
        ghome["powerwall"]["soe"] = soe
        ghome["powerwall"]["time"] = time.ctime(now)

    # Transition-based control is used to avoid repeated setpoint commands.
    if previous_on_grid and not on_grid and not is_thermostat_off:
        logging.warning("Power outage detected; turning off thermostat to conserve battery.")
        send_email(
            "Power Outage Detected",
            (
                "The connection to the grid was lost. "
                "The thermostat has been turned off to conserve energy."
            ),
        )
        send_pushover("Grid OFF")
        try:
            set_thermostat_mode("OFF")
            with lock:
                ghome["is_thermostat_off"] = True
                ghome["last_recovered_power"] = None
        except Exception as exc:
            logging.error("Failed to turn off thermostat: %s", exc)

    elif not previous_on_grid and on_grid:
        logging.info("Power has been restored.")
        with lock:
            ghome["low_battery_notified"] = False
        if is_thermostat_off:
            send_email(
                "Power Restored",
                (
                    "The connection to the grid has been restored. "
                    "The thermostat will be turned back on in 5 minutes."
                ),
            )
            send_pushover("Grid back ON")
            with lock:
                ghome["last_recovered_power"] = now

    # Delay HVAC recovery to avoid adding a sudden startup load after outage.
    if is_thermostat_off and on_grid and last_recovered_power:
        if now - last_recovered_power > POWER_RECOVERY_DELAY_SECONDS:
            if original_thermostat_mode and original_thermostat_mode != "OFF":
                logging.info(
                    "Recovery delay passed; restoring thermostat mode to '%s'.",
                    original_thermostat_mode,
                )
                try:
                    set_thermostat_mode(original_thermostat_mode)
                except Exception as exc:
                    logging.error("Failed to restore thermostat mode: %s", exc)
                else:
                    with lock:
                        ghome["is_thermostat_off"] = False
                        ghome["last_recovered_power"] = None
            else:
                logging.info("Original thermostat mode unavailable or OFF; no mode restore needed.")
                with lock:
                    ghome["is_thermostat_off"] = False
                    ghome["last_recovered_power"] = None

    if not on_grid and soe < LOW_BATTERY_THRESHOLD_PERCENT and not low_battery_notified:
        logging.warning("Powerwall state of energy is critically low at %.1f%%.", soe)
        send_email(
            "Critical Alert: Powerwall Battery Low",
            f"The Powerwall state of energy is critically low at {soe:.1f}%.",
        )
        with lock:
            ghome["low_battery_notified"] = True


def read_powerwall_status() -> None:
    """Continuously monitor Powerwall state and drive outage control logic."""
    while True:
        try:
            on_grid, soe = get_grid_status()
            process_powerwall_state(on_grid=on_grid, soe=soe)
        except Exception:
            # Keep worker alive; main thread monitors worker health and can restart process.
            logging.exception("Unhandled error in powerwall worker loop.")
        time.sleep(POWERWALL_POLL_SECONDS)


def _select_device_traits(hvac_status: Dict[str, Any]) -> Dict[str, Any]:
    """Select thermostat traits from SDM devices payload.

    Args:
        hvac_status: SDM list-devices response payload.

    Returns:
        Trait map for the selected thermostat device.

    Raises:
        KeyError: If no device payload is available.
    """
    devices: List[Dict[str, Any]] = hvac_status.get("devices", [])
    if not devices:
        raise KeyError("No devices found in thermostat status payload.")

    # Prefer explicit configured device; fallback to first available item.
    selected_device = devices[0]
    configured_device_suffix = f"/devices/{str(DEVICE_ID).strip().strip('/')}"
    for device in devices:
        name = str(device.get("name", ""))
        if name == _device_name() or name.endswith(configured_device_suffix):
            selected_device = device
            break
    return selected_device["traits"]


def read_thermostat_status() -> None:
    """Refresh thermostat status fields on a fixed polling interval."""
    while True:
        try:
            hvac_status = get_thermostat_status()
            traits = _select_device_traits(hvac_status)
            with lock:
                ghome["thermostat"]["time"] = time.ctime()
                if not ghome["is_thermostat_off"]:
                    ghome["thermostat"]["mode"] = traits["sdm.devices.traits.ThermostatMode"]["mode"]

                ghome["thermostat"]["is_eco"] = (
                    traits["sdm.devices.traits.ThermostatEco"]["mode"] == "MANUAL_ECO"
                )
                ghome["thermostat"]["ambient_temperature_celsius"] = traits[
                    "sdm.devices.traits.Temperature"
                ]["ambientTemperatureCelsius"]
                setpoints = traits["sdm.devices.traits.ThermostatTemperatureSetpoint"]
                ghome["thermostat"]["cool_celsius"] = setpoints.get("coolCelsius")
                ghome["thermostat"]["heat_celsius"] = setpoints.get("heatCelsius")

                ambient_temp = ghome["thermostat"]["ambient_temperature_celsius"]
            logging.info("Thermostat status updated: inside %.1f C.", ambient_temp)
        except (KeyError, IndexError, TypeError) as exc:
            logging.error("Failed to parse thermostat status due to unexpected payload: %s", exc)
        except Exception as exc:
            logging.error("Failed to update thermostat status: %s", exc)
        time.sleep(THERMOSTAT_POLL_SECONDS)


def run_service() -> None:
    """Start worker threads and exit if any worker dies.

    Exiting the process on worker death allows `systemd` to restart the service
    and avoids silent partial failure.
    """
    powerwall_worker = threading.Thread(
        target=read_powerwall_status,
        name="Powerwall and HVAC Control",
        daemon=True,
    )
    thermostat_worker = threading.Thread(
        target=read_thermostat_status,
        name="Thermostat Status Update",
        daemon=True,
    )
    powerwall_worker.start()
    thermostat_worker.start()

    while True:
        if not powerwall_worker.is_alive() or not thermostat_worker.is_alive():
            logging.critical(
                "Worker thread died (powerwall_alive=%s, thermostat_alive=%s); exiting for systemd restart.",
                powerwall_worker.is_alive(),
                thermostat_worker.is_alive(),
            )
            raise SystemExit(1)
        time.sleep(1)


if __name__ == "__main__":
    try:
        run_service()
    except KeyboardInterrupt:
        logging.info("Exiting service...")
