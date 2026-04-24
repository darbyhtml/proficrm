#!/bin/bash
# Host-level WAL-G push from spool directory.
# Called by /etc/cron.d/proficrm-walg-spool каждую минуту.
# Created 2026-04-23 W10.2-early B.1 host-pivot.

set -u

SPOOL=/var/lib/proficrm-staging/wal-spool
LOG=/var/log/proficrm-walg-spool.log
LOCKFILE=/var/run/proficrm-walg-push.lock

# Prevent concurrent runs (cron ticks every minute).
exec 200>"$LOCKFILE" || exit 0
if ! flock -n 200; then
  exit 0
fi

# Load R2 credentials from walg.env (chmod 600).
set -a
. /etc/wal-g/walg.env
set +a

# Process WAL files in spool (skip .tmp, oldest first).
for f in $(ls -1tr "$SPOOL" 2>/dev/null | grep -v "\.tmp$"); do
  SRC="$SPOOL/$f"
  [ -f "$SRC" ] || continue

  TS=$(date -u +"%FT%TZ")
  if /usr/local/bin/wal-g wal-push "$SRC" >> "$LOG" 2>&1; then
    rm -f "$SRC"
    echo "$TS pushed + removed: $f" >> "$LOG"
  else
    echo "$TS push FAILED: $f (keeping in spool for retry)" >> "$LOG"
    exit 1
  fi
done
