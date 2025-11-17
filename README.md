# Home Automation Power Management

This script monitors the grid connection status of a Tesla Powerwall and automatically manages a Google Nest Thermostat to conserve energy during a power outage. It sends email notifications when the power goes out and when it's restored.

## Features

- **Powerwall Monitoring**: Continuously checks for grid connectivity via the [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard).
- **Thermostat Control**: Automatically turns off the HVAC system during a power outage.
- **Smart Recovery**: Restores the thermostat to its previous mode 5 minutes after power is restored, preventing a sudden power surge.
- **Email Notifications**: Sends email alerts for power outages and restorations to a configurable list of recipients.
- **Service-Based**: Includes `systemd` unit files to run as a reliable background service on Linux.
- **File Logging**: All events and errors are logged to `/var/log/home-automation.log`.

## Prerequisites

- A Linux system with `systemd`.
- Python 3.8 or newer.
- A running instance of [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard) accessible on `http://localhost:8675`.
- Google API credentials for the Smart Device Management API.
- A Gmail account with an **App Password** for sending email notifications.

## Installation

1.  **Clone the Repository**
    ```bash
    git clone <your-repository-url>
    cd <repository-directory>
    ```

2.  **Install Dependencies**
    The script requires the `requests` library.
    ```bash
    pip install requests
    ```

3.  **Configure Secrets**
    The script requires a `_secrets.py` file to store your credentials. A template is provided.

    First, copy the template to its final location and open it for editing:
    ```bash
    sudo mkdir -p /etc/home-automation
    sudo cp _secrets.py.template /etc/home-automation/_secrets.py
    sudo nano /etc/home-automation/_secrets.py
    ```
    Fill in all the required credentials in this new file.

4.  **Run the Setup Script**
    The `setup.sh` script will copy all files to their correct locations, set permissions, and start the service.
    ```bash
    sudo ./setup.sh
    ```

## Managing the Service

You can manage the automation service using standard `systemctl` commands:

- **Check the status**:
  ```bash
  sudo systemctl status home-automation.service
  ```

- **View logs**:
  ```bash
  sudo journalctl -u home-automation.service -f
  ```
  Or view the log file directly:
  ```bash
  sudo tail -f /var/log/home-automation.log
  ```

- **Start the service**:
  ```bash
  sudo systemctl start home-automation.service
  ```

- **Stop the service**:
  ```bash
  sudo systemctl stop home-automation.service
  ```

- **Restart the service**:
  ```bash
  sudo systemctl restart home-automation.service
  ```
## Useful Resources
-  [Powerwall-Dashboard](https://github.com/jasonacox/Powerwall-Dashboard) a Monitoring Dashboard for Tesla Solar and Powerwall systems using Grafana, InfluxDB, Telegraf, and pyPowerwall.
-  [pyPowerwall](https://github.com/jasonacox/pypowerwall) a Python module to interface with Tesla Energy Gateways for Powerwall and solar power data.
-  [python-google-nest-sdm](https://github.com/allenporter/python-google-nest-sdm)  a library for Google Device Access using the Smart Device Management API.

## Author
Ondrej Chvala (<ochvala@gmail.com>)
