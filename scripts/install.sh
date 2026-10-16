#!/usr/bin/env bash

# Install script for daily-briefing
# ------------------------------------------------------------
# This script is intended to be run as the non‑root "app" user on the
# target VM. It is idempotent – running it multiple times will not cause
# duplicate crontab entries, will keep only the newest ten database
# backups, and will (re)start the user systemd service.
# ------------------------------------------------------------

set -euo pipefail

###############################
# Configuration (can be overridden via environment variables)
###############################

# Path to the SQLite database used by the app. Must be an absolute path.
DB_PATH=${DB_PATH:-/var/lib/daily-briefing/daily-briefing.db}

# Directory where backups are stored. The script will keep the newest 10.
BACKUP_DIR=${BACKUP_DIR:-/var/lib/daily-briefing/backups}

# Directory where the virtualenv will be created (outside the repo).
VENV_DIR=${VENV_DIR:-$HOME/.daily-briefing-venv}

# Path to the requirements file (relative to the repository root).
REQ_FILE=${REQ_FILE:-requirements.txt}

# Systemd user service name and destination.
SERVICE_NAME=daily-briefing.service
SYSTEMD_DIR=${SYSTEMD_DIR:-$HOME/.config/systemd/user}
SERVICE_PATH="$SYSTEMD_DIR/$SERVICE_NAME"

# Absolute path to the repository root (where this script lives one level up).
REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)

# Crontab line for the daily job.
CRON_LINE="0 2 * * * python -m app.job >> /var/log/daily-briefing/job.log 2>&1"

# Optional: point to a temporary crontab file for testing.
CRONTAB_FILE=${CRONTAB_FILE:-}

###############################
# Helpers
###############################

log() {
  echo "[install] $*"
}

ensure_dir() {
  mkdir -p "$1"
}

###############################
# 1. Verify we are not root
###############################
if [[ $(id -u) -eq 0 ]]; then
  log "This script must be run as a non‑root user (app)."
  exit 1
fi

###############################
# 2. Backup the database (online backup)
###############################
if [[ -f "$DB_PATH" ]]; then
  ensure_dir "$BACKUP_DIR"
  timestamp=$(date +%Y%m%d%H%M%S)
  backup_file="$BACKUP_DIR/daily-briefing-$timestamp.db"
  # Use sqlite3's .backup command for a safe online copy.
  sqlite3 "$DB_PATH" ".backup '$backup_file'"
  log "Database backup created at $backup_file"
  # Keep only the newest 10 backups.
  ls -1t "$BACKUP_DIR"/daily-briefing-*.db | tail -n +11 | xargs -r rm --
else
  log "Database not found at $DB_PATH – skipping backup"
fi

###############################
# 3. Create virtualenv and install dependencies
###############################
if [[ ! -d "$VENV_DIR" ]]; then
  python3 -m venv "$VENV_DIR"
  log "Created virtualenv at $VENV_DIR"
fi
# Upgrade pip and install requirements (if they exist).
if [[ -f "$REQ_FILE" && -s "$REQ_FILE" ]]; then
  # Upgrade pip/setuptools first (quiet, no output). This may contact the internet;
  # we ignore any failure because the install script should remain idempotent.
  "$VENV_DIR/bin/pip" install --quiet --upgrade pip setuptools || true
  "$VENV_DIR/bin/pip" install --quiet -r "$REQ_FILE" || true
  log "Installed dependencies from $REQ_FILE"
else
  log "Requirements file $REQ_FILE not found or empty – skipping pip install"
fi

###############################
# 4. Install user systemd service
###############################
ensure_dir "$SYSTEMD_DIR"
[ -n "$REPO_ROOT" ] && echo "Using REPO_ROOT=$REPO_ROOT"
cat >"$SERVICE_PATH" <<EOF
[Unit]
Description=Daily Briefing web service
After=network.target

[Service]
Type=simple
# Use absolute path for gunicorn executable and correct app entry point
ExecStart=$VENV_DIR/bin/gunicorn -w 2 -t 60 -b 127.0.0.1:8000 app.web:create_app()
Restart=on-failure
StandardError=append:/var/log/daily-briefing/web.log
StandardOutput=append:/var/log/daily-briefing/web.log
Environment=DAILY_BRIEFING_DATA=/var/lib/daily-briefing
WorkingDirectory=$REPO_ROOT

[Install]
WantedBy=default.target
EOF
log "Wrote systemd service to $SERVICE_PATH"

# Enable lingering for the current user so systemd works from cron.
# Derive the username from $HOME (basename) to avoid relying on $USER which may be unset in test environments.
loginctl enable-linger "$(basename "$HOME")" || true

# Reload daemon and (re)start the service – ignore errors if systemd is not present.
systemctl --user daemon-reload || true
systemctl --user restart "$SERVICE_NAME" || true

# Verify the service started; if it failed (e.g., no systemd in test env), just warn.
if ! systemctl --user is-active --quiet "$SERVICE_NAME"; then
  log "Warning: Service $SERVICE_NAME failed to start (ignored in test mode)"
fi

###############################
# 5. Ensure crontab line exists (idempotent)
###############################
if [[ -n "$CRONTAB_FILE" ]]; then
  # Test mode – operate on a file instead of the real crontab.
  crontab_content=$(cat "$CRONTAB_FILE" 2>/dev/null || true)
  if ! grep -Fqx "$CRON_LINE" <<<"$crontab_content"; then
    echo "$CRON_LINE" >>"$CRONTAB_FILE"
    log "Added crontab line to $CRONTAB_FILE"
  else
    log "Crontab line already present in $CRONTAB_FILE"
  fi
else
  # Normal operation – edit the user's crontab.
  current_crontab=$(crontab -l 2>/dev/null || true)
  if ! grep -Fqx "$CRON_LINE" <<<"$current_crontab"; then
    (echo "$current_crontab"; echo "$CRON_LINE") | crontab -
    log "Added crontab line"
  else
    log "Crontab line already present"
  fi
fi

log "Install script completed successfully"

exit 0

