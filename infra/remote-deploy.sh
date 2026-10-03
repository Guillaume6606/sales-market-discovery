#!/usr/bin/env bash
# Execute this file over SSH: compose commands must not consume a script heredoc.
set -euo pipefail

if [ "${1:-0}" != "0" ]; then
    echo "Quick deploy is disabled: production images require a rebuild. Use make deploy." >&2
    exit 1
fi

DC_PROD=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
PHASE="build"
BACKUP=""
report_failure() {
    local status=$?
    if [ "$status" -ne 0 ]; then
        echo "Deploy failed during ${PHASE}. No automatic database rollback was attempted." >&2
        if [ -n "$BACKUP" ]; then
            echo "Backup file: ${BACKUP} (use only if the backup step completed)." >&2
            echo "Inspect the failure before restarting applications or restoring data." >&2
        fi
    fi
}
trap report_failure EXIT

echo "--- Building images..."
"${DC_PROD[@]}" build --pull

PHASE="database readiness"
"${DC_PROD[@]}" up -d --no-recreate db redis
DB_READY=0
for ((attempt = 1; attempt <= 30; attempt++)); do
    if "${DC_PROD[@]}" exec -T db sh -c \
        'pg_isready -t 2 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
        DB_READY=1
        break
    fi
    sleep 2
 done
if [ "$DB_READY" != "1" ]; then
    echo "Database did not become ready after 30 attempts; applications were not stopped." >&2
    exit 1
fi

PHASE="stopping applications"
"${DC_PROD[@]}" stop --timeout 60 backend ingestion ui

PHASE="database backup"
umask 077
mkdir -p backups
BACKUP="$(pwd)/backups/pre-deploy-$(date -u +%Y%m%dT%H%M%SZ)-$$.dump"
"${DC_PROD[@]}" exec -T db sh -c \
    'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$BACKUP"
if [ ! -s "$BACKUP" ]; then
    echo "Database backup is empty; migration was not started." >&2
    exit 1
fi
echo "--- Backup complete: ${BACKUP}"

PHASE="database migration"
"${DC_PROD[@]}" run --rm --no-deps -T backend python -m alembic upgrade head

PHASE="starting applications"
"${DC_PROD[@]}" up -d --no-deps --force-recreate backend ingestion ui caddy

PHASE="application readiness"
echo "--- Checking application readiness (20 attempts)..."
for ((attempt = 1; attempt <= 20; attempt++)); do
    if curl -fsS --connect-timeout 2 --max-time 5 -o /dev/null \
        http://localhost:8000/health/ready 2>/dev/null; then
        echo "==> Health check PASSED"
        "${DC_PROD[@]}" ps
        exit 0
    fi
    sleep 3
done
echo "Health check failed; inspect application status and sanitized logs on the server." >&2
exit 1
