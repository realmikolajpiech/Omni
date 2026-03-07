#!/bin/bash
set -e

# build_installer.sh — One-shot build: PyInstaller + DMG packaging
# Usage: cd installer && ./build_installer.sh [--no-notarize]
#   --no-notarize   Build and sign the DMG but skip Apple notarization/stapling

NO_NOTARIZE=0
for arg in "$@"; do
    [ "$arg" = "--no-notarize" ] && NO_NOTARIZE=1
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

echo "========================================="
echo "  Omni Build"
echo "========================================="
echo ""

# ── Step 1: Check dependencies ────────────────────────────────────────────────
echo "==> Checking build dependencies..."

if ! command -v python3 &>/dev/null; then
    echo "Error: python3 not found. Install Python 3.12 via Homebrew."
    exit 1
fi

# Install PyInstaller + PyQt6 in the build environment if needed
python3 -c "import PyInstaller" 2>/dev/null || {
    echo "==> Installing PyInstaller..."
    pip3 install pyinstaller
}

python3 -c "import PyQt6" 2>/dev/null || {
    echo "==> Installing PyQt6..."
    pip3 install PyQt6
}

# ── Step 2: Run PyInstaller ────────────────────────────────────────────────────
echo ""
echo "==> Running PyInstaller..."
cd "$SCRIPT_DIR"
python3 -m PyInstaller installer.spec --noconfirm

echo ""
echo "==> PyInstaller complete. App at: dist/Omni.app"

# ── Step 3: Create DMG ────────────────────────────────────────────────────────
echo ""
echo "==> Creating DMG..."
chmod +x "$SCRIPT_DIR/create_dmg.sh"
if [ "$NO_NOTARIZE" = "1" ]; then
    "$SCRIPT_DIR/create_dmg.sh" "Omni" --no-notarize
else
    "$SCRIPT_DIR/create_dmg.sh" "Omni"
fi

echo ""
echo "========================================="
echo "  Build complete!"
echo "  Output: installer/Omni.dmg"
echo "========================================="
