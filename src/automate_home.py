"""
Reads status of Powerwall and disables HVAC to save energy during a power outage
Ondrej Chvala <ochvala@gmail.com>
"""
import threading
import time
import logging
import requests
import smtplib
import sys
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Add the configuration directory to the Python path to find _secrets.py.
sys.path.insert(0, '/etc/home-automation')
# Import secrets, including the new email list.
from _secrets import (
    CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN, PROJECT_ID, DEVICE_ID,
    GMAIL_ADDRESS, GMAIL_PASSWORD, NOTIFY_EMAILS
)

# Configure logging.
log_formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(threadName)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(log_formatter)
root_logger.addHandler(console_handler)
file_handler = logging.FileHandler("/var/log/home-automation/home-automation.log")
file_handler.setFormatter(log_formatter)
root_logger.addHandler(file_handler)


# Shared data object for thermostat and Powerwall status.
ghome = {
    "thermostat": {
        "time": None,  # Update time.
        "mode": None,  # Heating or cooling, remembered for power recovery.
        "is_eco": None,  # ECO mode status.
        "temp": None,  # Current temperature.
        "ambient_temperature_celsius": None,
        "cool_celsius": None,
        "heat_celsius": None,
    },
    "is_thermostat_off": False,  # Flag to indicate if the thermostat was turned off by this script.
    "last_recovered_power": None,  # Timestamp of power recovery to manage the 5-minute delay.
    "powerwall": {
        "time": None,  # Update time.
        "on_grid": True,  # Grid connection status, assume True at start.
        "soc": None  # State of charge.
    },
}

# Lock for thread-safe access to the shared ghome object.
lock = threading.Lock()


def send_email(subject, body):
    """Sends an email notification using Gmail to a list of recipients."""
    # Check if email credentials and recipient list are provided in secrets.
    if not all([GMAIL_ADDRESS, GMAIL_PASSWORD, NOTIFY_EMAILS]):
        logging.warning("Email credentials or recipient list not configured. Skipping email notification.")
        return

    try:
        # Create the email message.
        msg = MIMEMultipart()
        msg['From'] = GMAIL_ADDRESS
        msg['To'] = ", ".join(NOTIFY_EMAILS)  # Join the list for the 'To' header.
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        # Connect to the Gmail SMTP server.
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        # Log in to the server.
        server.login(GMAIL_ADDRESS, GMAIL_PASSWORD)
        # Send the email to the list of recipients.
        server.sendmail(GMAIL_ADDRESS, NOTIFY_EMAILS, msg.as_string())
        # Disconnect from the server.
        server.quit()
        logging.info("Email notification sent successfully.")
    except Exception as e:
        logging.error(f"Failed to send email: {e}")


def get_access_token():
    """Gets a new Google API access token using the refresh token."""
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


def set_thermostat_mode(hvac_mode: str = 'OFF'):
    """Sets the thermostat's HVAC mode (e.g., HEAT, COOL, OFF)."""
    assert hvac_mode in ['HEAT', 'COOL', 'HEATCOOL', 'OFF']

    ACCESS_TOKEN = get_access_token()
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{PROJECT_ID}/devices/{DEVICE_ID}:executeCommand"
    requests.post(url, headers=headers, json={
        "command": "sdm.devices.commands.Thermostat.SetMode",
        "params": {
            "mode": hvac_mode
        }
    }
                  )


def set_thermostat_ECO(eco_on: bool = False):
    """Sets the thermostat's ECO mode on or off."""
    if eco_on:
        mode = "MANUAL_ECO"
    else:
        mode = "OFF"

    ACCESS_TOKEN = get_access_token()
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{PROJECT_ID}/devices/{DEVICE_ID}:executeCommand"
    requests.post(url, headers=headers, json={
        "command": "sdm.devices.commands.ThermostatEco.SetMode",
        "params": {
            "mode": mode
        }
    }
                  )


def get_thermostat_status() -> dict:
    """Retrieves the current status of the thermostat from the Google API."""
    ACCESS_TOKEN = get_access_token()
    headers = {
        "Authorization": f"Bearer {ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    return requests.get(
        f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{PROJECT_ID}/devices/{DEVICE_ID}",
        headers=headers
    ).json()


def get_grid_status() -> bool:
    """Checks if the system is connected to the grid via the Powerwall Dashboard."""
    try:
        # This uses the local Powerwall-Dashboard instance.
        status: bool = 'SystemConnectedToGrid' in requests.get('http://localhost:8675/alerts', timeout=5).json()
    except requests.exceptions.RequestException as e:
        logging.error("Could not connect to Powerwall Dashboard: %s", e)
        # Assume on grid if connection fails to avoid shutting down HVAC unnecessarily.
        return True
    return status


def read_powerwall_status():
    """Continuously monitors Powerwall status and manages the thermostat during power outages."""
    while True:
        on_grid = get_grid_status()

        with lock:
            previous_on_grid = ghome["powerwall"]["on_grid"]
            is_thermostat_off = ghome["is_thermostat_off"]
            last_recovered_power = ghome["last_recovered_power"]
            original_thermostat_mode = ghome["thermostat"]["mode"]
            
            ghome["powerwall"]["on_grid"] = on_grid
            ghome["powerwall"]["time"] = time.ctime()

        # Power outage scenario: Grid was on, now it's off.
        if previous_on_grid and not on_grid and not is_thermostat_off:
            logging.warning("Power outage detected! Turning off thermostat to conserve energy.")
            send_email("Power Outage Detected", "The connection to the grid was lost. The thermostat has been turned off to conserve energy.")
            try:
                set_thermostat_mode('OFF')
                with lock:
                    ghome["is_thermostat_off"] = True
                    ghome["last_recovered_power"] = None  # Clear recovery time on new outage.
            except Exception as e:
                logging.error("Failed to turn off thermostat: %s", e)

        # Power recovery scenario: Grid was off, now it's on.
        elif not previous_on_grid and on_grid:
            logging.info("Power has been restored.")
            if is_thermostat_off:
                send_email("Power Restored", "The connection to the grid has been restored. The thermostat will be turned back on in 5 minutes.")
                with lock:
                    ghome["last_recovered_power"] = time.time()
        
        # Check for recovery completion: Thermostat is off and power has been on.
        if is_thermostat_off and on_grid and last_recovered_power:
            # Check if 5 minutes (300 seconds) have passed since recovery.
            if time.time() - last_recovered_power > 300:
                if original_thermostat_mode and original_thermostat_mode != 'OFF':
                    logging.info(f"5-minute recovery period has passed. Restoring thermostat mode to '{original_thermostat_mode}'.")
                    try:
                        set_thermostat_mode(original_thermostat_mode)
                        with lock:
                            ghome["is_thermostat_off"] = False
                            ghome["last_recovered_power"] = None # Reset for the next event.
                    except Exception as e:
                        logging.error(f"Failed to restore thermostat mode: {e}")
                else:
                    # If the original mode was OFF or unknown, just mark as available.
                    logging.info("Thermostat was originally OFF or mode is unknown. No changes made.")
                    with lock:
                        ghome["is_thermostat_off"] = False
                        ghome["last_recovered_power"] = None

        time.sleep(5)


def read_thermostat_status():
    """Periodically reads the thermostat's status and updates the shared ghome object."""
    while True:
        try:
            hvac_status = get_thermostat_status()
            with lock:
                ghome["thermostat"]["time"] = time.ctime()
                traits = hvac_status.get("traits", {})
                
                # Update thermostat mode only if not manually turned off by this script.
                if not ghome["is_thermostat_off"]:
                    thermostat_mode = traits.get("sdm.devices.traits.ThermostatMode", {})
                    ghome["thermostat"]["mode"] = thermostat_mode.get("mode")

                # ECO mode status.
                eco_mode = traits.get("sdm.devices.traits.ThermostatEco", {})
                ghome["thermostat"]["is_eco"] = eco_mode.get("mode") == "MANUAL_ECO"

                # Ambient temperature.
                temp_trait = traits.get("sdm.devices.traits.Temperature", {})
                ghome["thermostat"]["ambient_temperature_celsius"] = temp_trait.get("ambientTemperatureCelsius")

                # Temperature setpoints.
                setpoint_trait = traits.get("sdm.devices.traits.ThermostatTemperatureSetpoint", {})
                ghome["thermostat"]["cool_celsius"] = setpoint_trait.get("coolCelsius")
                ghome["thermostat"]["heat_celsius"] = setpoint_trait.get("heatCelsius")

            logging.info("Thermostat status updated.")
        except Exception as e:
            logging.error("Failed to update thermostat status: %s", e)

        # Wait for 1 hour before the next update.
        time.sleep(3600)


if __name__ == "__main__":
    # Create the worker threads.
    t1 = threading.Thread(target=read_powerwall_status, name="Power Wall and HVAC Control", daemon=True)
    t2 = threading.Thread(target=read_thermostat_status, name="Thermostat Status Update", daemon=True)

    # Start the worker threads.
    t1.start()
    t2.start()

    # Keep the main thread alive to allow daemon threads to run.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Exiting service...")