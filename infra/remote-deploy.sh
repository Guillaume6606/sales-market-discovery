#!/usr/bin/env bash
# Remote-side deploy steps, executed on the VPS by infra/deploy.sh.
#
# Must run from a file, NOT an ssh heredoc: `docker compose exec/run` attach
# stdin and steal script bytes when bash reads the script from its stdin,
# silently truncating execution mid-script.
set -euo pipefail

QUICK="${1:-0}"
DC_PROD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

if [ "$QUICK" = "0" ]; then
    echo "--- Building images..."
    $DC_PROD build --pull

    echo "--- Running migrations (before app services start)..."
    $DC_PROD up -d db
    until $DC_PROD exec -T db pg_isready -U "${POSTGRES_USER:-app}" >/dev/null 2>&1; do
        sleep 2
    done
    $DC_PROD run --rm --no-deps -T backend python -m alembic upgrade head
fi

echo "--- Starting services..."
# --force-recreate: compose does NOT recreate containers when the image is
# rebuilt under the same tag or when .env changes on disk
$DC_PROD up -d --force-recreate

echo "--- Health check (up to 60s)..."
STATUS="FAIL"
for _ in $(seq 1 20); do
    sleep 3
    if STATUS=$(curl -sf -o /dev/null -w "%{http_code}" http://localhost:8000/health 2>/dev/null); then
        break
    fi
    STATUS="FAIL"
done
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
