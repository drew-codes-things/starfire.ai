#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

case "$(uname -m)" in
  x86_64)  BUILD_ARCH="x64";   UNPACKED="linux-unpacked" ;;
  aarch64) BUILD_ARCH="arm64"; UNPACKED="linux-arm64-unpacked" ;;
  *) echo "Can't build for $(uname -m): only x86_64 and aarch64." >&2; exit 1 ;;
esac

[ -d node_modules ] || npm ci --no-audit --no-fund

echo "==> Running the tests"
npm test

echo "==> Packing the app"
npx electron-builder --linux dir "--$BUILD_ARCH"

echo "==> Building the AppImage"
npx electron-builder --linux AppImage "--$BUILD_ARCH" --prepackaged "release/$UNPACKED"

echo "==> Building the Debian package"
npx electron-builder --linux deb "--$BUILD_ARCH" --prepackaged "release/$UNPACKED"

APPIMAGE="$(ls -t release/starfire-*.AppImage 2>/dev/null | head -1 || true)"
DEB="$(ls -t release/starfire_*.deb 2>/dev/null | head -1 || true)"

echo
if [ -n "$APPIMAGE" ]; then
  echo "Done."
  echo "  $APPIMAGE ($(du -h "$APPIMAGE" | cut -f1))"
  [ -z "$DEB" ] || echo "  $DEB ($(du -h "$DEB" | cut -f1))"
else
  echo "No AppImage was produced." >&2
  exit 1
fi
