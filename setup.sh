#!/bin/bash

# This script installs the home automation service.
# It must be run with root privileges.

# 1. Check for root privileges
if [ "$EUID" -ne 0 ]; then
  echo "Please run this script as root or with sudo."
  exit 1
fi

echo "Starting home automation service setup..."

# 2. Define user and directories
SERVICE_USER="homeauto"
CONFIG_DIR="/etc/home-automation"
LOG_DIR="/var/log/home-automation"
SCRIPT_DEST="/usr/local/sbin/automate_home.py"
SERVICE_FILE="/etc/systemd/system/home-automation.service"

# 3. Create a dedicated user for the service
if id "$SERVICE_USER" &>/dev/null; then
    echo "User $SERVICE_USER already exists. Skipping creation."
else
    echo "Creating system user '$SERVICE_USER'..."
    useradd --system --shell /bin/false $SERVICE_USER
fi

# 4. Create directories
echo "Creating directories..."
mkdir -p "$CONFIG_DIR"
mkdir -p "$LOG_DIR"
chown -R "$SERVICE_USER":"$SERVICE_USER" "$LOG_DIR"

# 5. Copy application and configuration files
echo "Copying application and service files..."
cp src/automate_home.py "$SCRIPT_DEST"
cp etc/home-automation.service "$SERVICE_FILE"

# Make the script executable
chmod +x "$SCRIPT_DEST"

# 6. Handle secrets file
SECRETS_FILE="$CONFIG_DIR/_secrets.py"
if [ -f "$SECRETS_FILE" ]; then
    echo "Secrets file already exists at $SECRETS_FILE. Skipping template copy."
else
    echo "Copying _secrets.py.template to $SECRETS_FILE."
    cp src/_secrets.py.template "$SECRETS_FILE"
    chown "$SERVICE_USER":"$SERVICE_USER" "$SECRETS_FILE"
    chmod 600 "$SECRETS_FILE" # Restrict access to the owner
    echo "IMPORTANT: Please edit $SECRETS_FILE now and fill in your credentials."
    # Pause to allow user to edit
    read -p "Press Enter to continue after editing the secrets file..."
fi

# 7. Reload systemd, enable and start the service
echo "Reloading systemd daemon and starting the service..."
systemctl daemon-reload
systemctl enable home-automation.service
systemctl start home-automation.service

echo "-----------------------------------------------------"
echo "Setup complete!"
echo "The home automation service is now running."
echo "To check its status, run: sudo systemctl status home-automation.service"
echo "To view logs, run: sudo journalctl -u home-automation.service -f"
echo "-----------------------------------------------------"

exit 0

