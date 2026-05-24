#!/usr/bin/env bash
# Återställ notarkiv från en rclone-remote.
#
# OBS: Det här raderar nuvarande lokala data utan bekräftelse i sista steget.
# Stoppa appen och workern först.
#
# Användning:
#   ./scripts/restore.sh                  # senaste DB + senaste bilder
#   ./scripts/restore.sh 2026-05-24_1830  # specifik DB-snapshot

set -euo pipefail

REMOTE="${BACKUP_RCLONE_REMOTE:-gdrive}"
REMOTE_PATH="${BACKUP_RCLONE_PATH:-notarkiv-backup}"
SNAPSHOT="${1:-latest}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data"
DB_FILE="$DATA_DIR/notarkiv.db"
IMAGES_DIR="$DATA_DIR/images"

STAGE_DIR="$(mktemp -d -t notarkiv-restore-XXXXXX)"
trap 'rm -rf "$STAGE_DIR"' EXIT

if [ "$SNAPSHOT" = "latest" ]; then
  DB_REMOTE="$REMOTE:$REMOTE_PATH/db/notarkiv-latest.db.gz"
else
  DB_REMOTE="$REMOTE:$REMOTE_PATH/db/notarkiv-$SNAPSHOT.db.gz"
fi

echo "[restore] Hämtar DB från $DB_REMOTE"
rclone copyto "$DB_REMOTE" "$STAGE_DIR/notarkiv.db.gz"
gunzip "$STAGE_DIR/notarkiv.db.gz"

echo "[restore] Är appen och workern stoppade? (Ctrl+C för att avbryta)"
read -p "Tryck Enter för att skriva över $DB_FILE: "

mkdir -p "$DATA_DIR"
cp -v "$STAGE_DIR/notarkiv.db" "$DB_FILE"

echo "[restore] Synkar bilder till $IMAGES_DIR"
mkdir -p "$IMAGES_DIR"
rclone sync "$REMOTE:$REMOTE_PATH/images" "$IMAGES_DIR"

echo "[restore] Klar. Starta appen igen."
