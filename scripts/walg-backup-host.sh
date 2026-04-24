#!/bin/bash
# Host-level wal-g backup-push fallback.
# W10.2-early B.1 2026-04-23.
#
# Primary path: docker-compose.walg-backup.yml (with /etc/ssl/certs mount).
# This script is a fallback if containerised wal-g fails — uses host binary
# directly. Requires /var/lib/postgresql/data symlink to the pgdata docker
# volume (`ln -sfn /var/lib/docker/volumes/proficrm-staging_pgdata_staging/_data
# /var/lib/postgresql/data`).

set +x
set -a
. /etc/wal-g/walg.env
set +a
export PGHOST=127.0.0.1
export PGCONNECT_TIMEOUT=10

PGDATA=/var/lib/postgresql/data

echo "[host-backup] starting at $(date -u +%FT%TZ)"
time /usr/local/bin/wal-g backup-push "$PGDATA"
EXIT=$?
echo "[host-backup] exit=$EXIT at $(date -u +%FT%TZ)"
exit $EXIT
