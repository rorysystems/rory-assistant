#!/bin/sh
# Instalador do Rory Assistant para macOS e Linux (roda do código-fonte, sem instalador).
#
#   Instalar ou atualizar:  curl -fsSL https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.sh | sh
#   Desinstalar:            curl -fsSL https://raw.githubusercontent.com/rorysystems/rory-assistant/main/install.sh | sh -s -- --uninstall
#
# Opções (depois de `sh -s --`):  --uninstall  --purge (apaga os dados sem perguntar)  --keep-data
# Variáveis opcionais:
#   RORY_DEST=<pasta>        onde instalar o código
#   RORY_REF=<tag|main>      versão a baixar (padrão: a última Release; se não houver, main)
#   RORY_LOCAL_SRC=<pasta>   usa o código desta pasta em vez de baixar (testes)
#   RORY_NO_SHORTCUTS=1      não cria atalho/menu (testes)
#   RORY_PYTHON=3.14         versão do Python que o uv instala (Mac Intel usa 3.13)
#
# POSIX sh (no Ubuntu /bin/sh é o dash): sem recursos exclusivos do bash.

set -eu

REPO="rorysystems/rory-assistant"
NAME="Rory Assistant"
MODE="install"
PURGE=""
for arg in "$@"; do
  case "$arg" in
    --uninstall) MODE="uninstall" ;;
    --purge) PURGE="1" ;;
    --keep-data) PURGE="0" ;;
    *) echo "Opção desconhecida: $arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then C_B="$(printf '\033[36m')"; C_G="$(printf '\033[32m')"; C_Y="$(printf '\033[33m')"; C_R="$(printf '\033[31m')"; C_0="$(printf '\033[0m')"
else C_B=""; C_G=""; C_Y=""; C_R=""; C_0=""; fi
say()  { printf '%s%s%s\n' "$C_B" "$*" "$C_0"; }
ok()   { printf '  %s%s%s\n' "$C_G" "$*" "$C_0"; }
warn() { printf '  %s%s%s\n' "$C_Y" "$*" "$C_0"; }
die()  { printf '\n%sERRO: %s%s\n' "$C_R" "$*" "$C_0" >&2
         printf '%sNada foi apagado dos seus dados. Se persistir, veja "Problemas comuns" no README.%s\n' "$C_R" "$C_0" >&2
         exit 1; }

# ------------------------------------------------------------------ sistema
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin)
    DEFAULT_DEST="$HOME/Library/Application Support/Rory Assistant App"
    DATA_DIR="$HOME/Library/Application Support/$NAME"
    APP_BUNDLE="$HOME/Applications/$NAME.app"
    ;;
  Linux)
    DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
    DEFAULT_DEST="$DATA_HOME/rory-assistant"
    DATA_DIR="$DATA_HOME/$NAME"
    DESKTOP_FILE="$DATA_HOME/applications/rory-assistant.desktop"
    ICON_FILE="$DATA_HOME/icons/hicolor/256x256/apps/rory-assistant.png"
    BIN_LINK="$HOME/.local/bin/rory-assistant"
    ;;
  *) die "Sistema não suportado: $OS. Este script atende macOS e Linux; no Windows use o install.ps1." ;;
esac
DEST="${RORY_DEST:-$DEFAULT_DEST}"
SRC="$DEST/src"
VENV="$DEST/venv"

# ------------------------------------------------------------------ desinstalar
if [ "$MODE" = "uninstall" ]; then
  say "Desinstalando o $NAME..."
  if [ -d "$VENV" ] && command -v pgrep >/dev/null 2>&1 && pgrep -f "$VENV/bin/python" >/dev/null 2>&1; then
    die "O $NAME está aberto. Feche a janela dele e rode o comando de novo."
  fi
  if [ "$OS" = "Darwin" ]; then
    [ -d "$APP_BUNDLE" ] && rm -rf "$APP_BUNDLE" && ok "Removido: $APP_BUNDLE"
  else
    for f in "$DESKTOP_FILE" "$ICON_FILE" "$BIN_LINK"; do [ -e "$f" ] || [ -L "$f" ] && rm -f "$f" && ok "Removido: $f"; done
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DESKTOP_FILE")" >/dev/null 2>&1 || true
  fi
  [ -d "$DEST" ] && rm -rf "$DEST" && ok "Pasta removida: $DEST"
  if [ -d "$DATA_DIR" ]; then
    if [ -z "$PURGE" ]; then
      PURGE="0"
      if [ -r /dev/tty ]; then
        printf "Apagar também as conversas e configurações em '%s'? (s/N) " "$DATA_DIR" >/dev/tty
        read -r answer </dev/tty || answer=""
        case "$answer" in s|S|y|Y) PURGE="1" ;; esac
      fi
    fi
    if [ "$PURGE" = "1" ]; then rm -rf "$DATA_DIR"; ok "Dados apagados."; else ok "Dados mantidos em $DATA_DIR"; fi
  fi
  say "Pronto. (O uv e o Python que ele baixou ficam no PC; para remover: uv cache clean)"
  exit 0
fi

# ------------------------------------------------------------------ pré-checagens
say "Instalando o $NAME em $DEST"
PY="${RORY_PYTHON:-3.14}"
if [ "$OS" = "Darwin" ]; then
  MACOS_MAJOR="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
  if [ "$ARCH" = "x86_64" ]; then
    # o onnxruntime (dependência do markitdown) não tem wheel de Mac Intel para o Python 3.14
    [ -n "${RORY_PYTHON:-}" ] || PY="3.13"
  elif [ -n "$MACOS_MAJOR" ] && [ "$MACOS_MAJOR" -lt 14 ]; then
    die "No Mac com chip Apple (M1/M2/...) é preciso macOS 14 (Sonoma) ou mais novo; este é o macOS $MACOS_MAJOR."
  fi
fi
if [ "$OS" = "Linux" ] && [ "$ARCH" != "x86_64" ] && [ "$ARCH" != "aarch64" ]; then
  die "Arquitetura não suportada: $ARCH (só x86_64 e aarch64)."
fi
command -v curl >/dev/null 2>&1 || die "Preciso do curl. No Ubuntu/Debian: sudo apt install curl"
command -v tar >/dev/null 2>&1 || die "Preciso do tar. No Ubuntu/Debian: sudo apt install tar"

if [ -d "$VENV" ] && command -v pgrep >/dev/null 2>&1 && pgrep -f "$VENV/bin/python" >/dev/null 2>&1; then
  die "O $NAME está aberto. Feche a janela dele e rode o comando de novo."
fi
mkdir -p "$DEST"

# ------------------------------------------------------------------ uv
say "1/5 Verificando o uv (gerenciador do Python)"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  warn "uv não encontrado: instalando pelo instalador oficial (astral.sh), sem sudo."
  curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null || die "Não consegui instalar o uv. Veja https://docs.astral.sh/uv/getting-started/installation/ e rode de novo."
  command -v uv >/dev/null 2>&1 || die "O uv foi instalado, mas não está no PATH. Abra um terminal novo e rode o comando de novo."
fi
UV="$(command -v uv)"
ok "uv: $UV"

# ------------------------------------------------------------------ código
say "2/5 Baixando o código"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/rory-install.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT INT TERM
if [ -n "${RORY_LOCAL_SRC:-}" ]; then
  FRESH="$RORY_LOCAL_SRC"
  VERSION="local"
else
  REF="${RORY_REF:-}"
  if [ -z "$REF" ]; then
    REF="$(curl -fsSL -H 'User-Agent: rory-installer' "https://api.github.com/repos/$REPO/releases/latest" 2>/dev/null \
      | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -n 1 || true)"
    if [ -z "$REF" ]; then warn "Nenhuma Release encontrada: usando a branch main."; REF="main"; fi
  fi
  mkdir -p "$WORK/extract"
  fetch() { curl -fsSL "$1" | tar -xz -C "$WORK/extract" --strip-components=1; }
  if ! fetch "https://github.com/$REPO/archive/refs/tags/$REF.tar.gz" 2>/dev/null; then
    rm -rf "$WORK/extract"; mkdir -p "$WORK/extract"
    if ! fetch "https://github.com/$REPO/archive/refs/heads/$REF.tar.gz" 2>/dev/null; then
      [ "$REF" != "main" ] && warn "Versão '$REF' não encontrada: usando a branch main."
      rm -rf "$WORK/extract"; mkdir -p "$WORK/extract"
      REF="main"
      fetch "https://github.com/$REPO/archive/refs/heads/main.tar.gz" || die "Não consegui baixar o código do GitHub. Veja a internet/proxy e tente de novo."
    fi
  fi
  [ -f "$WORK/extract/requirements.txt" ] || die "O arquivo baixado não tem o formato esperado (sem requirements.txt)."
  FRESH="$WORK/extract"
  VERSION="$REF"
fi
# troca só o código; o venv e os dados do usuário ficam
rm -rf "$SRC"; mkdir -p "$SRC"
for item in app ui assets scripts requirements.txt requirements-linux.txt README.md; do
  [ -e "$FRESH/$item" ] && cp -R "$FRESH/$item" "$SRC/"
done
printf '%s\n' "$VERSION" > "$DEST/versao.txt"
ok "Versão: $VERSION"

# ------------------------------------------------------------------ python + dependências
say "3/5 Preparando o Python $PY e as dependências (na primeira vez baixa ~500 MB)"
if [ ! -x "$VENV/bin/python" ]; then
  "$UV" venv -q "$VENV" --python "$PY" || die "O uv não conseguiu criar o ambiente Python $PY."
fi
set -- -r "$SRC/requirements.txt"
[ "$OS" = "Linux" ] && set -- "$@" -r "$SRC/requirements-linux.txt"
"$UV" pip install -q --python "$VENV/bin/python" "$@" || die "A instalação das dependências falhou. Veja as mensagens acima."

if [ "$OS" = "Linux" ]; then
  # Qt precisa de algumas bibliotecas do sistema; ldd mostra as que faltam.
  PYQT_DIR="$("$VENV/bin/python" -I -c 'import os, PyQt6; print(os.path.dirname(PyQt6.__file__))' 2>/dev/null || true)"
  MISSING=""
  if [ -n "$PYQT_DIR" ] && command -v ldd >/dev/null 2>&1; then
    for lib in "$PYQT_DIR"/Qt6/plugins/platforms/libqxcb.so "$PYQT_DIR"/Qt6/lib/libQt6WebEngineCore.so.6 "$PYQT_DIR"/Qt6/libexec/QtWebEngineProcess "$PYQT_DIR"/Qt6/lib/libQt6Widgets.so.6; do
      [ -e "$lib" ] && MISSING="$MISSING $(ldd "$lib" 2>/dev/null | sed -n 's/^[[:space:]]*\(.*\) => not found.*/\1/p')"
    done
    MISSING="$(printf '%s\n' $MISSING | sort -u | tr '\n' ' ')"
  fi
  if [ -n "${MISSING# }" ]; then
    PKGS=""
    UNKNOWN=""
    t64() { if apt-cache show "$1t64" >/dev/null 2>&1; then echo "$1t64"; else echo "$1"; fi; }
    for so in $MISSING; do
      case "$so" in
        libxcb-cursor.so.*) p=libxcb-cursor0 ;;
        libxcb-icccm.so.*) p=libxcb-icccm4 ;;
        libxcb-image.so.*) p=libxcb-image0 ;;
        libxcb-keysyms.so.*) p=libxcb-keysyms1 ;;
        libxcb-render-util.so.*) p=libxcb-render-util0 ;;
        libxcb-shape.so.*) p=libxcb-shape0 ;;
        libxcb-xkb.so.*) p=libxcb-xkb1 ;;
        libxcb-randr.so.*) p=libxcb-randr0 ;;
        libxcb-xinerama.so.*) p=libxcb-xinerama0 ;;
        libxcb-sync.so.*) p=libxcb-sync1 ;;
        libxcb-shm.so.*) p=libxcb-shm0 ;;
        libxcb-xfixes.so.*) p=libxcb-xfixes0 ;;
        libxcb-render.so.*) p=libxcb-render0 ;;
        libxcb-glx.so.*) p=libxcb-glx0 ;;
        libxcb-dri3.so.*) p=libxcb-dri3-0 ;;
        libxkbcommon-x11.so.*) p=libxkbcommon-x11-0 ;;
        libxkbcommon.so.*) p=libxkbcommon0 ;;
        libxkbfile.so.*) p=libxkbfile1 ;;
        libEGL.so.*) p=libegl1 ;;
        libGL.so.*) p=libgl1 ;;
        libgbm.so.*) p=libgbm1 ;;
        libdrm.so.*) p=libdrm2 ;;
        libnss3.so|libnssutil3.so|libsmime3.so) p=libnss3 ;;
        libnspr4.so) p=libnspr4 ;;
        libXcomposite.so.*) p=libxcomposite1 ;;
        libXdamage.so.*) p=libxdamage1 ;;
        libXrandr.so.*) p=libxrandr2 ;;
        libXtst.so.*) p=libxtst6 ;;
        libXfixes.so.*) p=libxfixes3 ;;
        libXi.so.*) p=libxi6 ;;
        libXcursor.so.*) p=libxcursor1 ;;
        libXrender.so.*) p=libxrender1 ;;
        libXext.so.*) p=libxext6 ;;
        libX11-xcb.so.*) p=libx11-xcb1 ;;
        libxshmfence.so.*) p=libxshmfence1 ;;
        libfontconfig.so.*) p=libfontconfig1 ;;
        libdbus-1.so.*) p=libdbus-1-3 ;;
        libasound.so.*) p="$(t64 libasound2)" ;;
        libglib-2.0.so.*|libgobject-2.0.so.*|libgio-2.0.so.*) p="$(t64 libglib2.0-0)" ;;
        *) UNKNOWN="$UNKNOWN $so"; p="" ;;
      esac
      case " $PKGS " in *" $p "*) ;; *) [ -n "$p" ] && PKGS="$PKGS $p" ;; esac
    done
    echo
    warn "Faltam bibliotecas do sistema para a janela (Qt). Rode:"
    if [ -n "${PKGS# }" ]; then
      printf '    %ssudo apt install%s%s\n' "$C_B" "$PKGS" "$C_0"
    fi
    if [ -n "${UNKNOWN# }" ]; then
      warn "Sem pacote conhecido para:$UNKNOWN (em outras distribuições, instale o pacote que traz essas bibliotecas)."
    fi
    warn "Depois é só abrir o app. O resto da instalação já está pronto."
    LIBS_MISSING=1
  fi
fi

if ( cd "$SRC" && "$VENV/bin/python" -I -c "import sys; sys.path.insert(0, '.'); import app.main" ) >/dev/null 2>&1; then
  ok "Dependências OK."
else
  ( cd "$SRC" && "$VENV/bin/python" -I -c "import sys; sys.path.insert(0, '.'); import app.main" ) || true
  die "O app foi instalado, mas não abre (falha ao importar; mensagem acima)."
fi

# ------------------------------------------------------------------ atalho
say "4/5 Criando o atalho"
if [ "${RORY_NO_SHORTCUTS:-}" = "1" ]; then
  warn "RORY_NO_SHORTCUTS=1: atalho ignorado."
elif [ "$OS" = "Darwin" ]; then
  CONTENTS="$APP_BUNDLE/Contents"
  rm -rf "$APP_BUNDLE"
  mkdir -p "$CONTENTS/MacOS" "$CONTENTS/Resources"
  cat > "$CONTENTS/MacOS/launcher" <<EOF
#!/bin/bash
# Gerado pelo install.sh do Rory Assistant. Apps abertos pelo Finder não herdam o PATH do terminal.
export PATH="/opt/homebrew/bin:/usr/local/bin:\$HOME/.local/bin:\$PATH"
cd "$SRC" || exit 1
exec "$VENV/bin/python" -m app.main
EOF
  chmod +x "$CONTENTS/MacOS/launcher"
  cat > "$CONTENTS/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>com.rorysystems.assistant</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Para ditar mensagens por voz.</string>
</dict>
</plist>
EOF
  # ícone: .icns a partir do PNG 1024 (sips/iconutil já vêm no macOS)
  ICONSET="$WORK/icon.iconset"
  mkdir -p "$ICONSET"
  if [ -f "$SRC/assets/icon-1024.png" ] && command -v sips >/dev/null 2>&1 && command -v iconutil >/dev/null 2>&1; then
    for s in 16 32 64 128 256 512; do
      sips -z $s $s "$SRC/assets/icon-1024.png" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
      sips -z $((s*2)) $((s*2)) "$SRC/assets/icon-1024.png" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$CONTENTS/Resources/icon.icns" || warn "Não consegui gerar o ícone; o app abre mesmo assim."
  fi
  ok "$APP_BUNDLE"
else
  mkdir -p "$(dirname "$DESKTOP_FILE")" "$(dirname "$ICON_FILE")" "$(dirname "$BIN_LINK")"
  cp "$SRC/assets/icon-256.png" "$ICON_FILE"
  # PYWEBVIEW_GUI=qt: usa o Qt do pip em vez de procurar GTK/WebKit no sistema
  cat > "$DEST/rory-assistant" <<EOF
#!/bin/sh
# Gerado pelo install.sh do Rory Assistant.
export PYWEBVIEW_GUI=qt
cd "$SRC" || exit 1
exec "$VENV/bin/python" -m app.main "\$@"
EOF
  chmod +x "$DEST/rory-assistant"
  ln -sf "$DEST/rory-assistant" "$BIN_LINK"
  cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=$NAME
Comment=Assistente de desenvolvimento com Claude Code
Exec="$DEST/rory-assistant"
Icon=$ICON_FILE
Terminal=false
Categories=Development;
StartupNotify=false
EOF
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$(dirname "$DESKTOP_FILE")" >/dev/null 2>&1 || true
  ok "$DESKTOP_FILE"
  ok "Comando: rory-assistant (em ~/.local/bin)"
fi

# ------------------------------------------------------------------ claude
say "5/5 Verificando o Claude Code"
CLAUDE="$(cd "$SRC" && "$VENV/bin/python" -I -c "import sys; sys.path.insert(0, '.'); from app import config; print(config.find_claude() or '')" 2>/dev/null || true)"
if [ -z "$CLAUDE" ]; then
  warn "Não achei o Claude Code. Ele deveria vir junto com as dependências; rode o comando de novo."
elif "$CLAUDE" auth status 2>/dev/null | grep -q '"loggedIn"[[:space:]]*:[[:space:]]*true'; then
  ok "Claude Code com login feito."
else
  warn "Falta fazer login no Claude Code (precisa de assinatura Claude). Duas opções:"
  warn "  - abra o app: Configurações → \"Entrar na conta\"; ou"
  warn "  - rode no terminal:  \"$CLAUDE\" auth login"
fi

echo
say "Pronto! Abra o '$NAME' pelo $( [ "$OS" = "Darwin" ] && echo 'Launchpad/Spotlight (pasta ~/Applications)' || echo 'menu de aplicativos' )."
[ "${LIBS_MISSING:-}" = "1" ] && warn "Lembre de instalar as bibliotecas listadas acima antes de abrir."
echo "Para atualizar, rode o mesmo comando de instalação de novo. Seus dados ficam em $DATA_DIR"
