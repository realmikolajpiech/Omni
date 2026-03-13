#!/bin/bash
set -e

DMG="${1:-}"

if [ -z "$DMG" ]; then
  # Try to find a .dmg in common build output locations
  DMG=$(find . -maxdepth 3 -name "*.dmg" ! -path "*/node_modules/*" | head -1)
fi

if [ -z "$DMG" ] || [ ! -f "$DMG" ]; then
  echo "Usage: ./upload-dmg.sh path/to/Omni.dmg"
  exit 1
fi

BUCKET="omni-releases"
KEY="Omni.dmg"
WRANGLER="./worker/node_modules/.bin/wrangler"

echo "Uploading $(basename "$DMG") → r2://$BUCKET/$KEY ..."
"$WRANGLER" r2 object put "$BUCKET/$KEY" --file "$DMG" --content-type "application/octet-stream" --config ./worker/wrangler.toml

echo "Done. Available at: https://releases.heyomni.app/Omni.dmg"
