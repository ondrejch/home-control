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
        "is_eco" : None         # ECO on or off
    },
    "powerwall" : {
        "on_grid" : None,       # grid status
    }
    "fast_update" : None,
    "slow_update" : None
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


def fast_updater():
    """Updates ghome every 5 seconds."""
    while True:
        with lock:
            ghome["fast_update"] = f"Updated at {time.ctime()}"
            logging.info("Fast update: %s", ghome["fast_update"])
        time.sleep(5)


def slow_updater():
    """Updates ghome every hour."""
    while True:
        with lock:
            ghome["slow_update"] = f"Updated at {time.ctime()}"
            logging.info("Slow update: %s", ghome["slow_update"])
        time.sleep(3600)  # 1 hour


if __name__ == "__main__":
    # Create threads
    t1 = threading.Thread(target=fast_updater, name="FastThread", daemon=True)
    t2 = threading.Thread(target=slow_updater, name="SlowThread", daemon=True)

    # Start threads
    t1.start()
    t2.start()

    # Keep main thread alive
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Exiting service...")

