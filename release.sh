#!/bin/bash
set -e

# release.sh — Tag and publish a GitHub release for Omni
#
# Usage: ./release.sh --notes <notes> [--dmg]
#   --notes     Release notes text
#   --dmg       Upload installer/Omni.dmg as a release asset
#
# Tag and title are derived from APP_VERSION in src/core/config.py

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export SCRIPT_DIR

NOTES=""
INCLUDE_DMG=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --notes)    NOTES="$2";  shift 2 ;;
        --dmg)      INCLUDE_DMG=1; shift ;;
        *)          shift ;;
    esac
done

VERSION=$(python3 << 'EOF'
import re, sys, os
txt = open(os.environ['SCRIPT_DIR'] + '/src/core/config.py').read()
m = re.search(r"APP_VERSION\s*=\s*[\"']([^\"']+)[\"']", txt)
print(m.group(1) if m else sys.exit('APP_VERSION not found'))
EOF
)
TAG="v$VERSION"
TITLE="v$VERSION"
NOTES="${NOTES:-Release $TAG}"

DMG="$SCRIPT_DIR/installer/Omni.dmg"

echo "========================================="
echo "  Omni Release  $TAG"
echo "========================================="
echo ""

if [ "$INCLUDE_DMG" -eq 1 ] && [ ! -f "$DMG" ]; then
    echo "Error: $DMG not found. Run installer/build_installer.sh first."
    exit 1
fi

# ── Step 1: Tag and push ───────────────────────────────────────────────────────
echo "==> Tagging $TAG and pushing…"
git -C "$SCRIPT_DIR" tag "$TAG"
git -C "$SCRIPT_DIR" push origin "$TAG"

# ── Step 2: Create GitHub release ─────────────────────────────────────────────
echo ""
echo "==> Creating GitHub release $TAG…"
ASSETS=()
[ "$INCLUDE_DMG" -eq 1 ] && ASSETS+=("$DMG#Omni.dmg")

gh release create "$TAG" \
    --repo realmikolajpiech/Omni \
    --title "$TITLE" \
    --notes "$NOTES" \
    "${ASSETS[@]}"

echo ""
echo "========================================="
echo "  Released: $TAG"
echo "  https://github.com/realmikolajpiech/Omni/releases/tag/$TAG"
echo "========================================="
