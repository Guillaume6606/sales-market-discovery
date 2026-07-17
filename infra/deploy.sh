#!/usr/bin/env bash
# Deploy market-discovery to VPS via rsync + SSH.
#
# Usage:
#   make deploy          # full: sync → build → migrate → restart → health check
#   make deploy-quick    # quick: sync → restart only (no rebuild, no migrate)
#
# Requires: SSH_HOST in .deploy.env or environment.
set -euo pipefail

# ── Config ──────────────────────────────────────────────────
: "${SSH_HOST:?Set SSH_HOST in .deploy.env}"
: "${SSH_USER:=root}"
: "${SSH_PORT:=22}"
: "${DEPLOY_DIR:=/opt/market-discovery}"

QUICK="${SSH_QUICK:-0}"
DC_PROD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

RSYNC_EXCLUDES=(
    .venv .git .github .claude .ruff_cache .pytest_cache .mypy_cache
    __pycache__ htmlcov node_modules
    docs/ tests/ reports/
    "*.pyc" "*.pyo"
    "*.egg-info" ".~lock.*"
    .env .deploy.env
    backups/
)

# ── Sync code ───────────────────────────────────────────────
echo "==> Syncing to ${SSH_USER}@${SSH_HOST}:${DEPLOY_DIR} (port ${SSH_PORT})"
rsync -azP --delete \
    -e "ssh -p ${SSH_PORT}" \
    $(printf -- "--exclude=%s " "${RSYNC_EXCLUDES[@]}") \
    . "${SSH_USER}@${SSH_HOST}:${DEPLOY_DIR}/"

# ── Remote deploy ───────────────────────────────────────────
# Executed from the rsynced file, NOT an ssh heredoc: docker compose
# exec/run attach stdin and steal heredoc bytes, truncating the script.
echo "==> Running deploy on remote (quick=${QUICK})"
ssh -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" \
    "cd '${DEPLOY_DIR}' && bash infra/remote-deploy.sh '${QUICK}'"

DOMAIN="${DOMAIN:-unknown}"
echo ""
echo "==> Deploy complete!"
echo "    Dashboard: https://${DOMAIN}"
echo "    Health:    https://${DOMAIN}/health"
