#!/bin/bash
set -e

# create_dmg.sh — Signs Omni.app, builds a styled DMG, notarizes and staples.
#
# The DMG gets a custom dark background, large icons positioned like Raycast/Arc,
# and no Finder toolbar — a clean drag-to-Applications experience.
#
# Usage: ./create_dmg.sh [output_name]

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_NAME="${1:-Omni}"
NO_NOTARIZE=0
for arg in "$@"; do
    [ "$arg" = "--no-notarize" ] && NO_NOTARIZE=1
done
OUTPUT_DMG="${SCRIPT_DIR}/${OUTPUT_NAME}.dmg"
APP_PATH="${SCRIPT_DIR}/dist/Omni.app"
STAGING_DIR="${SCRIPT_DIR}/.dmg_staging"
TEMP_DMG="${SCRIPT_DIR}/.temp_omni.dmg"
ENTITLEMENTS="${SCRIPT_DIR}/resources/entitlements.plist"
BG_SCRIPT="${SCRIPT_DIR}/resources/generate_dmg_background.py"

SIGN_ID="Developer ID Application: Daniel Piech (D3S4G4R6GM)"
KEYCHAIN_PROFILE="notarytool-password"

# ── DMG window layout ─────────────────────────────────────────────────────────
# 660×420 pt window — centered near top-left of screen
WIN_X=200;    WIN_Y=120
WIN_W=660;    WIN_H=420
WIN_RIGHT=$((WIN_X + WIN_W))
WIN_BOTTOM=$((WIN_Y + WIN_H))

ICON_SIZE=128       # icon pt size
TEXT_SIZE=13        # label font size

# Icon centres in window coordinates (points)
# Horizontally: app at 27%, Applications at 73% of width
# Vertically:   centred at ~47% of height
APP_X=178;   APP_Y=192
APPS_X=482;  APPS_Y=192

echo "========================================="
echo "  Omni DMG Build"
echo "========================================="
echo ""

# ── Guard ─────────────────────────────────────────────────────────────────────
echo "==> Checking for Omni.app…"
if [ ! -d "$APP_PATH" ]; then
    echo "Error: '$APP_PATH' not found. Run PyInstaller first."
    exit 1
fi

# ── Step 1: Sign .app ─────────────────────────────────────────────────────────
echo ""
echo "==> Signing .app bundle…"

find "$APP_PATH" \( -name "*.dylib" -o -name "*.so" -o -name "*.framework" \) \
    | while read -r f; do
        codesign --force --verify --sign "$SIGN_ID" --options runtime "$f" 2>/dev/null || true
    done

codesign --force --verify --verbose \
    --sign "$SIGN_ID" \
    --options runtime \
    --entitlements "$ENTITLEMENTS" \
    --deep \
    "$APP_PATH"

echo "==> Signed."

# ── Step 2: Staging directory ─────────────────────────────────────────────────
echo ""
echo "==> Preparing staging directory…"
rm -rf "$STAGING_DIR"
mkdir -p "$STAGING_DIR"

# Hidden background folder — Finder reads background.png from here
mkdir -p "${STAGING_DIR}/.background"
echo "==> Generating background image…"
python3 -c "import PIL" 2>/dev/null || pip3 install Pillow -q
python3 "$BG_SCRIPT" "${STAGING_DIR}/.background/background.png"

cp -R "$APP_PATH" "$STAGING_DIR/"
ln -s /Applications "${STAGING_DIR}/Applications"

# Mark .background invisible so Finder doesn't show it
chflags hidden "${STAGING_DIR}/.background"

# ── Step 3: Create writable DMG ───────────────────────────────────────────────
echo ""
echo "==> Creating writable DMG…"
rm -f "$TEMP_DMG"

# Size: app size × 1.5 + 20 MB buffer (plenty for .DS_Store + background)
APP_MB=$(du -sm "$APP_PATH" | awk '{print $1}')
DMG_MB=$(( APP_MB * 3 / 2 + 20 ))

hdiutil create \
    -volname "$OUTPUT_NAME" \
    -srcfolder "$STAGING_DIR" \
    -ov \
    -format UDRW \
    -fs HFS+ \
    -size "${DMG_MB}m" \
    "$TEMP_DMG"

# ── Step 4: Mount + style with Finder ─────────────────────────────────────────
echo ""
echo "==> Mounting for styling…"

# Detach any stale volume with the same name so we mount as /Volumes/Omni
hdiutil detach "/Volumes/$OUTPUT_NAME" -quiet 2>/dev/null || true
sleep 1

MOUNT_OUTPUT=$(hdiutil attach -readwrite -noverify -noautoopen "$TEMP_DMG")
MOUNT_DIR=$(echo "$MOUNT_OUTPUT" | grep '/Volumes/' | awk -F'\t' '{print $NF}' | tail -1)
DISK_NAME=$(basename "$MOUNT_DIR")
echo "    Mounted at: $MOUNT_DIR"

sleep 2

# Hide macOS-generated metadata folders
[ -d "$MOUNT_DIR/.fseventsd" ] && chflags hidden "$MOUNT_DIR/.fseventsd"

echo "==> Styling Finder window via AppleScript…"
/usr/bin/osascript << APPLESCRIPT
tell application "Finder"
    tell disk "$DISK_NAME"
        open

        set current view of container window to icon view
        set toolbar visible of container window to false
        set statusbar visible of container window to false
        set sidebar width of container window to 0

        set the bounds of container window to {$WIN_X, $WIN_Y, $WIN_RIGHT, $WIN_BOTTOM}

        set opts to the icon view options of container window
        set arrangement of opts to not arranged
        set icon size of opts to $ICON_SIZE
        set text size of opts to $TEXT_SIZE
        set shows item info of opts to false
        set label position of opts to bottom

        set background picture of opts to (POSIX file "$MOUNT_DIR/.background/background.png") as alias

        set position of item "Omni.app"      of container window to {$APP_X,  $APP_Y}
        set position of item "Applications"  of container window to {$APPS_X, $APPS_Y}

        update without registering applications
        delay 5
        close
    end tell
end tell
APPLESCRIPT

sleep 2
/usr/bin/osascript << APPLESCRIPT2
tell application "Finder"
    tell disk "$DISK_NAME"
        open
        update without registering applications
        delay 3
        close
    end tell
end tell
APPLESCRIPT2

echo "==> Syncing and unmounting…"
sleep 2
sync
sync
sleep 2
hdiutil detach "$MOUNT_DIR" -quiet

# ── Step 5: Convert to compressed read-only DMG ───────────────────────────────
echo ""
echo "==> Converting to read-only compressed DMG…"
rm -f "$OUTPUT_DMG"
hdiutil convert "$TEMP_DMG" \
    -format UDZO \
    -imagekey zlib-level=9 \
    -o "$OUTPUT_DMG"

echo "==> Cleaning up staging files…"
rm -f "$TEMP_DMG"
rm -rf "$STAGING_DIR"

# ── Step 6: Sign the DMG ──────────────────────────────────────────────────────
echo ""
echo "==> Signing DMG…"
codesign --force --verify --sign "$SIGN_ID" "$OUTPUT_DMG"
echo "==> DMG signed."

# ── Step 7: Notarize + Staple (skipped with --no-notarize) ───────────────────
if [ "$NO_NOTARIZE" = "1" ]; then
    echo ""
    echo "==> Skipping notarization (--no-notarize)."
    echo ""
    echo "========================================="
    echo "  DMG ready (unsigned): $OUTPUT_DMG"
    echo "========================================="
    du -sh "$OUTPUT_DMG"
else
    echo ""
    echo "==> Submitting to Apple for notarization (a few minutes)…"
    xcrun notarytool submit "$OUTPUT_DMG" \
        --keychain-profile "$KEYCHAIN_PROFILE" \
        --wait

    echo ""
    echo "==> Stapling notarization ticket…"
    xcrun stapler staple "$OUTPUT_DMG"

    echo ""
    echo "========================================="
    echo "  DMG ready: $OUTPUT_DMG"
    echo "========================================="
    xcrun stapler validate "$OUTPUT_DMG"
    du -sh "$OUTPUT_DMG"
fi
