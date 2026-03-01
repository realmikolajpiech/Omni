#!/bin/bash
set -e

# create_dmg.sh — Packages Install Omni.app into a compressed read-only DMG
# Usage: ./create_dmg.sh [output_name]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_NAME="${1:-Omni Installer}"
OUTPUT_DMG="${SCRIPT_DIR}/${OUTPUT_NAME}.dmg"
APP_PATH="${SCRIPT_DIR}/dist/Install Omni.app"
STAGING_DIR="${SCRIPT_DIR}/.dmg_staging"
TEMP_DMG="${SCRIPT_DIR}/.temp_installer.dmg"

echo "==> Checking for Install Omni.app..."
if [ ! -d "$APP_PATH" ]; then
    echo "Error: '$APP_PATH' not found. Run PyInstaller first."
    exit 1
fi

echo "==> Cleaning up old artifacts..."
rm -rf "$STAGING_DIR"
rm -f "$TEMP_DMG"
rm -f "$OUTPUT_DMG"

echo "==> Creating staging directory..."
mkdir -p "$STAGING_DIR"
cp -R "$APP_PATH" "$STAGING_DIR/"

# Create a symlink to /Applications for drag-to-install UX
ln -s /Applications "$STAGING_DIR/Applications"

echo "==> Creating temporary writable DMG..."
hdiutil create \
    -volname "Install Omni" \
    -srcfolder "$STAGING_DIR" \
    -ov \
    -format UDRW \
    "$TEMP_DMG"

echo "==> Converting to compressed read-only DMG..."
hdiutil convert "$TEMP_DMG" \
    -format UDZO \
    -imagekey zlib-level=9 \
    -o "$OUTPUT_DMG"

echo "==> Cleaning up staging files..."
rm -rf "$STAGING_DIR"
rm -f "$TEMP_DMG"

echo ""
echo "========================================="
echo "  DMG created: $OUTPUT_DMG"
echo "========================================="
du -sh "$OUTPUT_DMG"
