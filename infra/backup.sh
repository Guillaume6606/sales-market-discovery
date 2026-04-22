#!/usr/bin/env bash
# Daily PostgreSQL backup for market-discovery.
# Installed via crontab by setup-vps.sh (daily 5:30 UTC).
# Manual: make backup
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/market-discovery}"
BACKUP_DIR="${DEPLOY_DIR}/backups"
RETENTION_DAYS=14
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
DC_PROD="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

cd "$DEPLOY_DIR"
mkdir -p "$BACKUP_DIR"

# Dump via the running db container
$DC_PROD exec -T db pg_dump \
    -U "${POSTGRES_USER:-app}" \
    -d "${POSTGRES_DB:-app}" \
    --no-owner --no-acl \
    | gzip > "${BACKUP_DIR}/db_${TIMESTAMP}.sql.gz"

# Prune old backups
find "$BACKUP_DIR" -name "db_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

SIZE=$(du -h "${BACKUP_DIR}/db_${TIMESTAMP}.sql.gz" | cut -f1)
echo "[$(date)] Backup complete: db_${TIMESTAMP}.sql.gz (${SIZE})"
