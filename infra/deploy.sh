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
echo "==> Running deploy on remote (quick=${QUICK})"
ssh -p "${SSH_PORT}" "${SSH_USER}@${SSH_HOST}" bash -s -- "${QUICK}" "${DEPLOY_DIR}" <<'REMOTE'
set -euo pipefail
QUICK="$1"
DEPLOY_DIR="$2"
DC_PROD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

cd "$DEPLOY_DIR"

if [ "$QUICK" = "0" ]; then
    echo "--- Building images..."
    $DC_PROD build --pull

    echo "--- Running migrations (before app services start)..."
    $DC_PROD up -d db
    until $DC_PROD exec -T db pg_isready -U "${POSTGRES_USER:-app}" >/dev/null 2>&1; do
        sleep 2
    done
    $DC_PROD run --rm --no-deps backend python -m alembic upgrade head
fi

echo "--- Starting services..."
# --force-recreate: compose does NOT recreate containers when the image is
# rebuilt under the same tag or when .env changes on disk
$DC_PROD up -d --force-recreate

echo "--- Health check..."
sleep 3
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null || echo "FAIL")
if [ "$STATUS" = "200" ]; then
    echo "==> Health check PASSED"
else
    echo "==> Health check FAILED (status: $STATUS)"
    echo "--- Last 30 lines of logs:"
    $DC_PROD logs --tail=30
    exit 1
fi

echo ""
echo "--- Container status:"
$DC_PROD ps
REMOTE

DOMAIN="${DOMAIN:-unknown}"
echo ""
echo "==> Deploy complete!"
echo "    Dashboard: https://${DOMAIN}"
echo "    Health:    https://${DOMAIN}/health"
