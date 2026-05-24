#!/usr/bin/env bash
# Backup av notarkiv till en rclone-remote. Tänkt att köras via cron på värden.
#
# Förkrav:
# - rclone installerat och konfigurerat (`rclone config` en gång)
# - sqlite3 installerat
# - Miljövariablerna BACKUP_RCLONE_REMOTE och BACKUP_RCLONE_PATH satta
#   (eller anges som argument)
#
# Användning:
#   ./scripts/backup.sh                  # läser env eller default
#   ./scripts/backup.sh gdrive notarkiv  # explicit remote + path

set -euo pipefail

REMOTE="${1:-${BACKUP_RCLONE_REMOTE:-gdrive}}"
REMOTE_PATH="${2:-${BACKUP_RCLONE_PATH:-notarkiv-backup}}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$PROJECT_ROOT/data"
DB_FILE="$DATA_DIR/notarkiv.db"
IMAGES_DIR="$DATA_DIR/images"

if [ ! -f "$DB_FILE" ]; then
  echo "FEL: DB-filen saknas: $DB_FILE" >&2
  exit 1
fi

TIMESTAMP=$(date +%Y-%m-%d_%H%M)
STAGE_DIR="$(mktemp -d -t notarkiv-backup-XXXXXX)"
trap 'rm -rf "$STAGE_DIR"' EXIT

echo "[backup] Snapshot av DB till $STAGE_DIR/notarkiv.db"
sqlite3 "$DB_FILE" ".backup $STAGE_DIR/notarkiv.db"

# Gzip för mindre transfer
echo "[backup] Komprimerar"
gzip -9 "$STAGE_DIR/notarkiv.db"

# Behåll senaste på en känd path + en timestampad kopia
echo "[backup] Laddar upp till $REMOTE:$REMOTE_PATH"
rclone copyto "$STAGE_DIR/notarkiv.db.gz" "$REMOTE:$REMOTE_PATH/db/notarkiv-latest.db.gz"
rclone copyto "$STAGE_DIR/notarkiv.db.gz" "$REMOTE:$REMOTE_PATH/db/notarkiv-$TIMESTAMP.db.gz"

# Bilder synkas inkrementellt
if [ -d "$IMAGES_DIR" ]; then
  echo "[backup] Synkar bilder"
  rclone sync "$IMAGES_DIR" "$REMOTE:$REMOTE_PATH/images" --fast-list
fi

# Rensa gamla DB-snapshots (behåll senaste 30)
echo "[backup] Rensar gamla DB-snapshots"
rclone delete --min-age 30d "$REMOTE:$REMOTE_PATH/db" --include "notarkiv-*.db.gz" || true

echo "[backup] Klar"
