#!/usr/bin/env bash
# Gera dist/installer/RoryAssistant-<versao>-linux-x86_64.tar.gz com install.sh dentro.
# Precisa no sistema (build e uso): GTK3 + WebKit2GTK (pywebview), ex. no Ubuntu:
#   sudo apt install python3-gi gir1.2-webkit2-4.1 libgirepository-2.0-dev libcairo2-dev
set -euo pipefail
cd "$(dirname "$0")/.."

python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt pyinstaller "pywebview[gtk]"
VERSION=$(.venv/bin/python -c "from app.config import APP_VERSION; print(APP_VERSION)")

.venv/bin/python scripts/make_icons.py
.venv/bin/pyinstaller --noconfirm --distpath dist --workpath build packaging/rory-assistant.spec

PKG="build/RoryAssistant-${VERSION}-linux"
rm -rf "$PKG"; mkdir -p "$PKG"
cp -R "dist/Rory Assistant" "$PKG/app"
cp assets/icon-256.png "$PKG/icon.png"
cat > "$PKG/install.sh" <<'EOF'
#!/usr/bin/env bash
# Instala em ~/.local (sem sudo) e cria o atalho no menu de aplicativos.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/rory-assistant"
rm -rf "$DEST"; mkdir -p "$DEST" "$HOME/.local/share/applications" "$HOME/.local/bin"
cp -R "$HERE/app/." "$DEST/"
cp "$HERE/icon.png" "$DEST/icon.png"
ln -sf "$DEST/Rory Assistant" "$HOME/.local/bin/rory-assistant"
cat > "$HOME/.local/share/applications/rory-assistant.desktop" <<DESK
[Desktop Entry]
Name=Rory Assistant
Comment=Assistente de desenvolvimento com Claude Code
Exec="$DEST/Rory Assistant"
Icon=$DEST/icon.png
Terminal=false
Type=Application
Categories=Development;
DESK
echo "Instalado. Abra pelo menu ou rode: rory-assistant"
command -v claude >/dev/null || echo "Aviso: instale o Claude Code: curl -fsSL https://claude.ai/install.sh | bash"
EOF
chmod +x "$PKG/install.sh"

mkdir -p dist/installer
tar -C build -czf "dist/installer/RoryAssistant-${VERSION}-linux-x86_64.tar.gz" "$(basename "$PKG")"
echo "ok: dist/installer/RoryAssistant-${VERSION}-linux-x86_64.tar.gz"
