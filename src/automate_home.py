import threading
import time
import logging
import requests

from _secrets import CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, PROJECT_ID, DEVICE_ID


# Configure logging (standard syntax)
logging.basicConfig(
    level=logging.INFO,                      # Log level
    format="%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

# Shared data object
ghome = {
    "thermostat" : {
        "mode" : None,          # heating or cooling, remember when power goes back
        "is_eco": None,         # ECO on or off
        "temp": None
    },
    "is_thermostat_off": None,   # this is set when HVAC is disabled
    "last_recovered_power" : None,  # when did the power come on, wait 5 minutes before restarting HVAC
    "powerwall" : {
        "on_grid" : None,       # grid status
        "soc": None         # state of charge
    },
}


# Lock to prevent race conditions
lock = threading.Lock()

def get_access_token():
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token"
        }
    )
    return resp.json()["access_token"]


def set_thermostat_ECO(eco_on: bool = False):
    if eco_on:
        mode = "MANUAL_ECO"
    else:
        mode = "OFF"

    ACCESS_TOKEN = get_access_token()
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"https://smartdevicemanagement.googleapis.com/v1/{PROJECT_ID}/devices/{DEVICE_ID}:executeCommand"
    requests.post(url, headers=headers, json={
            "command": "sdm.devices.commands.ThermostatEco.SetMode",
            "params": {
                "mode": "MANUAL_ECO"
            }
        }
    )


def get_thermostat_status() -> dict:
    ACCESS_TOKEN = get_access_token()
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    return requests.get(
        f"https://smartdevicemanagement.googleapis.com/v1/{PROJECT_ID}/devices",
        headers=headers
    ).json()


def get_grid_status() -> bool:
    """ This uses local https://github.com/jasonacox/Powerwall-Dashboard """
    status: bool = 'SystemConnectedToGrid' in requests.get('http://localhost:8675/alerts').json()
    return status


def read_powerwall_status():
    """Updates ghome every 5 seconds."""
    while True:
        with lock:
            ghome["fast_update"] = f"Updated at {time.ctime()}"
            logging.info("Fast update: %s", ghome["fast_update"])
        time.sleep(5)


def read_thermostat_status():
    """Updates ghome every hour."""
    while True:
        with lock:
            """traits:
  sdm.devices.traits.Connectivity:
    status: ONLINE
  sdm.devices.traits.Fan:
    timer_mode: 'ON'
    timer_timeout: '2025-11-17T04:00:44+00:00'
  sdm.devices.traits.Humidity:
    ambient_humidity_percent: 54.0
  sdm.devices.traits.Info:
    custom_name: Hallway Thermostat
  sdm.devices.traits.Temperature:
    ambient_temperature_celsius: 22.472885
  sdm.devices.traits.ThermostatEco:
    available_modes:
    - 'OFF'
    - MANUAL_ECO
    cool_celsius: 26.666666
    heat_celsius: 15.555555
    mode: 'OFF'
  sdm.devices.traits.ThermostatHvac:
    status: 'OFF'
  sdm.devices.traits.ThermostatMode:
    available_modes:
    - HEAT
    - COOL
    - HEATCOOL
    - 'OFF'
    mode: HEAT
  sdm.devices.traits.ThermostatTemperatureSetpoint:
    cool_celsius: null
    heat_celsius: 18.333334
type: sdm.devices.types.THERMOSTAT
"""
            hvac_status = get_thermostat_status()
            ghome[""] = f"Updated at {time.ctime()}"
            logging.info("Slow update: %s", ghome["slow_update"])
        time.sleep(3600)  # 1 hour


if __name__ == "__main__":
    # Create threads
    t1 = threading.Thread(target=read_powerwall_status, name="Power Wall status update", daemon=True)
    t2 = threading.Thread(target=read_thermostat_status, name="Thermostat status update", daemon=True)

    # Start threads
    t1.start()
    t2.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Exiting service...")
