#!/usr/bin/env bash
# Deploy market-discovery to VPS via rsync + SSH.
#
# Usage:
#   make deploy          # full: sync → build → migrate → restart → health check
#
# Requires: SSH_HOST in .deploy.env or environment.
set -euo pipefail

# ── Config ──────────────────────────────────────────────────
: "${SSH_HOST:?Set SSH_HOST in .deploy.env}"
: "${SSH_USER:=root}"
: "${SSH_PORT:=22}"
: "${DEPLOY_DIR:=/opt/market-discovery}"

QUICK="${SSH_QUICK:-0}"
if [ "$QUICK" != "0" ]; then
    echo "Quick deploy is disabled: production images require a rebuild. Use make deploy." >&2
    exit 1
fi

RSYNC_EXCLUDES=(
    .venv .git .github .claude .codex .agents .ruff_cache .pytest_cache .mypy_cache
    __pycache__ htmlcov node_modules
    docs/ tests/ reports/
    "*.pyc" "*.pyo"
    "*.egg-info" ".~lock.*"
    ".env*" "*.env" "*.env.*"
    credentials.json "*-credentials.json" "*.pem" "*.key"
    .coverage "*.log" .DS_Store .idea .vscode
    backups/ dist/ build/
)

RSYNC_ARGS=()
for pattern in "${RSYNC_EXCLUDES[@]}"; do
    RSYNC_ARGS+=("--exclude=$pattern")
done

# ── Sync code ───────────────────────────────────────────────
echo "==> Syncing to ${SSH_USER}@${SSH_HOST}:${DEPLOY_DIR} (port ${SSH_PORT})"
rsync -azP --delete \
    -e "ssh -p ${SSH_PORT}" \
    "${RSYNC_ARGS[@]}" \
    . "${SSH_USER}@${SSH_HOST}:${DEPLOY_DIR}/"

# ── Remote deploy ───────────────────────────────────────────
# Executed from the rsynced file, NOT an ssh heredoc: docker compose
# exec/run attach stdin and steal heredoc bytes, truncating the script.
echo "==> Running deploy on remote"
ssh -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" \
    "cd '${DEPLOY_DIR}' && bash infra/remote-deploy.sh '${QUICK}'"

DOMAIN="${DOMAIN:-unknown}"
echo ""
echo "==> Deploy complete!"
echo "    Dashboard: https://${DOMAIN}"
echo "    Health:    https://${DOMAIN}/health"
