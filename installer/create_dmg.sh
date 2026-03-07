#!/bin/bash
set -e

# create_dmg.sh — Signs Install Omni.app, packages into DMG, notarizes, and staples
# Usage: ./create_dmg.sh [output_name]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_NAME="${1:-Omni Installer}"
OUTPUT_DMG="${SCRIPT_DIR}/${OUTPUT_NAME}.dmg"
APP_PATH="${SCRIPT_DIR}/dist/Install Omni.app"
STAGING_DIR="${SCRIPT_DIR}/.dmg_staging"
TEMP_DMG="${SCRIPT_DIR}/.temp_installer.dmg"
ENTITLEMENTS="${SCRIPT_DIR}/resources/entitlements.plist"

SIGN_ID="Developer ID Application: Daniel Piech (D3S4G4R6GM)"
KEYCHAIN_PROFILE="notarytool-password"

echo "==> Checking for Install Omni.app..."
if [ ! -d "$APP_PATH" ]; then
    echo "Error: '$APP_PATH' not found. Run PyInstaller first."
    exit 1
fi

# ── Step 1: Sign the .app ─────────────────────────────────────────────────────
echo ""
echo "==> Signing .app bundle..."

# Sign all dylibs and .so files first (bottom-up)
find "$APP_PATH" \( -name "*.dylib" -o -name "*.so" -o -name "*.framework" \) | while read -r f; do
    codesign --force --verify --sign "$SIGN_ID" --options runtime "$f" 2>/dev/null || true
done

# Sign the main executable and bundle
codesign --force --verify --verbose \
    --sign "$SIGN_ID" \
    --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --deep \
    "$APP_PATH"

echo "==> App signed successfully."

# ── Step 2: Build the DMG ─────────────────────────────────────────────────────
echo ""
echo "==> Cleaning up old artifacts..."
rm -rf "$STAGING_DIR"
rm -f "$TEMP_DMG"
rm -f "$OUTPUT_DMG"

echo "==> Creating staging directory..."
mkdir -p "$STAGING_DIR"
cp -R "$APP_PATH" "$STAGING_DIR/"
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

# ── Step 3: Sign the DMG ──────────────────────────────────────────────────────
echo ""
echo "==> Signing DMG..."
codesign --force --verify --sign "$SIGN_ID" "$OUTPUT_DMG"
echo "==> DMG signed."

# ── Step 4: Notarize ──────────────────────────────────────────────────────────
echo ""
echo "==> Submitting to Apple for notarization (this takes a few minutes)..."
xcrun notarytool submit "$OUTPUT_DMG" \
    --keychain-profile "$KEYCHAIN_PROFILE" \
    --wait

# ── Step 5: Staple ────────────────────────────────────────────────────────────
echo ""
echo "==> Stapling notarization ticket to DMG..."
xcrun stapler staple "$OUTPUT_DMG"

echo ""
echo "========================================="
echo "  DMG ready: $OUTPUT_DMG"
echo "========================================="
xcrun stapler validate "$OUTPUT_DMG"
du -sh "$OUTPUT_DMG"
