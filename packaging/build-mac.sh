#!/usr/bin/env bash
# Gera dist/installer/RoryAssistant-<versao>-macOS.dmg (rodar num Mac ou no GitHub Actions).
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt pyinstaller
VERSION=$(.venv/bin/python -c "from app.config import APP_VERSION; print(APP_VERSION)")

.venv/bin/python scripts/make_icons.py
# .icns a partir do PNG 1024
ICONSET=build/icon.iconset
rm -rf "$ICONSET"; mkdir -p "$ICONSET"
for s in 16 32 64 128 256 512; do
  sips -z $s $s assets/icon-1024.png --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
  sips -z $((s*2)) $((s*2)) assets/icon-1024.png --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o assets/icon.icns

.venv/bin/pyinstaller --noconfirm --distpath dist --workpath build packaging/rory-assistant.spec

mkdir -p dist/installer
STAGE=build/dmg
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "dist/Rory Assistant.app" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "Rory Assistant" -srcfolder "$STAGE" -ov -format UDZO \
  "dist/installer/RoryAssistant-${VERSION}-macOS.dmg"
echo "ok: dist/installer/RoryAssistant-${VERSION}-macOS.dmg"
