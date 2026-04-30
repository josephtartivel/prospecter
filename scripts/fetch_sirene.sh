#!/usr/bin/env bash
# Fetch the SIRENE bulk dump from data.gouv.fr. Idempotent — re-running
# skips files already on disk. ~2 GB compressed, ~16 GB unzipped.
set -euo pipefail

cd "$(dirname "$0")/.."
DEST="data/sirene"
mkdir -p "$DEST"

base="https://files.data.gouv.fr/insee-sirene"
files=(
  "StockUniteLegale_utf8.zip"
  "StockEtablissement_utf8.zip"
)

for f in "${files[@]}"; do
  if [[ -f "$DEST/${f%.zip}.csv" ]]; then
    echo "skip $f (already unzipped)"
    continue
  fi
  if [[ ! -f "$DEST/$f" ]]; then
    echo "fetching $f…"
    curl -L --fail --progress-bar -o "$DEST/$f" "$base/$f"
  fi
  echo "unzipping $f…"
  unzip -o "$DEST/$f" -d "$DEST"
  rm "$DEST/$f"
done

echo "done — files in $DEST/:"
ls -lh "$DEST"
