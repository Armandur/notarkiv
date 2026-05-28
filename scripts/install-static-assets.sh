#!/usr/bin/env bash
# Laddar ned tunga klientside-bibliotek som inte committas i repot.
# Idempotent - hoppar över filer som redan finns med rätt storlek.
#
# Kör efter git clone, eller via Docker build-step i prod.

set -euo pipefail

cd "$(dirname "$0")/.."

STATIC_JS="app/static/js"
mkdir -p "$STATIC_JS"

download() {
  local url="$1"
  local dest="$2"
  local min_size="${3:-1000}"

  if [[ -f "$dest" ]]; then
    local size
    size=$(stat -c %s "$dest" 2>/dev/null || stat -f %z "$dest")
    if (( size >= min_size )); then
      echo "OK     $dest ($size bytes, behåller)"
      return 0
    fi
  fi
  echo "Hämtar $dest..."
  curl -sSfL "$url" -o "$dest"
  local size
  size=$(stat -c %s "$dest" 2>/dev/null || stat -f %z "$dest")
  echo "Klar   $dest ($size bytes)"
}

# OpenCV.js (~9 MB) - används av jscanify för dokumentkant-detektion i scan/quick.
# Version 4.5.5 är vad jscanify@1.2.0 testat mot.
download \
  "https://cdn.jsdelivr.net/npm/jscanify@1.2.0/src/opencv.js" \
  "$STATIC_JS/opencv.js" \
  1000000

echo "Statiska bibliotek på plats."
