#!/usr/bin/env bash
# One-time VPS provisioning for market-discovery.
# Run from local machine: make setup-vps
# Idempotent — safe to re-run.
set -euo pipefail

: "${SSH_HOST:?Set SSH_HOST in .deploy.env}"
: "${SSH_USER:=root}"
: "${SSH_PORT:=22}"
: "${DEPLOY_DIR:=/opt/market-discovery}"

echo "==> Provisioning ${SSH_USER}@${SSH_HOST} (port ${SSH_PORT})"

ssh -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" bash -s -- "${DEPLOY_DIR}" <<'REMOTE'
set -euo pipefail
DEPLOY_DIR="$1"

echo "--- Updating system packages..."
apt-get update -qq && apt-get upgrade -y -qq

# Install Docker if missing
if ! command -v docker &>/dev/null; then
    echo "--- Installing Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
else
    echo "--- Docker already installed: $(docker --version)"
fi

# Ensure docker compose plugin is available
if ! docker compose version &>/dev/null; then
    echo "ERROR: docker compose plugin not found. Install it manually."
    exit 1
fi

# Create deploy directory
echo "--- Creating ${DEPLOY_DIR}/backups"
mkdir -p "${DEPLOY_DIR}/backups"

# Install systemd service
SERVICE_SRC="${DEPLOY_DIR}/infra/systemd/market-discovery.service"
SERVICE_DST="/etc/systemd/system/market-discovery.service"
if [ -f "$SERVICE_SRC" ]; then
    echo "--- Installing systemd service..."
    cp "$SERVICE_SRC" "$SERVICE_DST"
    systemctl daemon-reload
    systemctl enable market-discovery
else
    echo "--- Systemd service file not found at ${SERVICE_SRC}"
    echo "    Run 'make deploy' first, then re-run 'make setup-vps' to install it."
fi

# Install backup cron (daily at 5:30 AM UTC)
BACKUP_SCRIPT="${DEPLOY_DIR}/infra/backup.sh"
CRON_LINE="30 5 * * * ${BACKUP_SCRIPT} >> /var/log/market-discovery-backup.log 2>&1"
if crontab -l 2>/dev/null | grep -qF "market-discovery"; then
    echo "--- Backup cron already installed"
else
    echo "--- Installing backup cron (daily 5:30 UTC)..."
    (crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
fi

# Basic firewall
if command -v ufw &>/dev/null; then
    echo "--- Configuring firewall (ufw)..."
    ufw allow 22/tcp   >/dev/null 2>&1 || true
    ufw allow 80/tcp   >/dev/null 2>&1 || true
    ufw allow 443/tcp  >/dev/null 2>&1 || true
    ufw --force enable  >/dev/null 2>&1 || true
    echo "    Allowed: SSH (22), HTTP (80), HTTPS (443)"
else
    echo "--- ufw not found, skipping firewall setup"
fi

echo ""
echo "==> VPS provisioning complete!"
echo ""
echo "Next steps:"
echo "  1. Copy your .env file to the VPS:"
echo "     scp .env ${DEPLOY_DIR}/.env"
echo "  2. Run the first deploy:"
echo "     make deploy"
REMOTE
