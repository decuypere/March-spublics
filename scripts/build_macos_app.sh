#!/usr/bin/env bash
# Construit deux applications macOS lancables depuis le Dock ou le Launchpad:
#
#   "Veille marches publics.app"  ouvre l'interface de consultation
#   "Veille - Collecte.app"       lance une collecte immediate
#
# Usage:  ./scripts/build_macos_app.sh [--port 8080] [--dest ~/Applications]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$HOME/Applications"
PORT=""

while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Option inconnue: $1" >&2; exit 1 ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "Ce script construit une application macOS et doit tourner sur un Mac." >&2
  exit 1
fi

# Port: --port, sinon celui de la configuration, sinon 8080
if [ -z "$PORT" ]; then
  PORT="$(awk '/^web:/{f=1;next} f&&/^  port:/{print $2;exit} f&&/^[^ ]/{f=0}' \
    "$PROJECT_DIR/config/config.yaml" 2>/dev/null || true)"
fi
PORT="${PORT:-8080}"

VENV_VEILLE="$PROJECT_DIR/.venv/bin/veille"
if [ ! -x "$VENV_VEILLE" ]; then
  echo "Attention: $VENV_VEILLE est introuvable."
  echo "Installez d'abord le projet:  python3 -m venv .venv && .venv/bin/pip install -e ."
  echo "L'application sera construite quand meme, mais ne demarrera pas tant que"
  echo "l'environnement n'existe pas."
fi

echo "Projet      : $PROJECT_DIR"
echo "Destination : $DEST"
echo "Port        : $PORT"

# ---------------------------------------------------------------- icone ----
ICONSET="$(mktemp -d)/veille.iconset"
ICNS=""
mkdir -p "$ICONSET"
PNG="$(dirname "$ICONSET")/icon.png"

if python3 "$PROJECT_DIR/scripts/make_icon.py" "$PNG" >/dev/null 2>&1 \
   && command -v sips >/dev/null && command -v iconutil >/dev/null; then
  for size in 16 32 64 128 256 512; do
    sips -z $size $size "$PNG" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1
    double=$((size * 2))
    sips -z $double $double "$PNG" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1
  done
  if iconutil -c icns "$ICONSET" -o "$(dirname "$ICONSET")/veille.icns" >/dev/null 2>&1; then
    ICNS="$(dirname "$ICONSET")/veille.icns"
    echo "Icone       : generee"
  fi
fi
[ -n "$ICNS" ] || echo "Icone       : ignoree (sips/iconutil indisponibles)"

# ------------------------------------------------------------ assemblage ----
make_app() {
  local app_name="$1" bundle_id="$2" script_body="$3"
  local app="$DEST/$app_name.app"

  rm -rf "$app"
  mkdir -p "$app/Contents/MacOS" "$app/Contents/Resources"

  cat > "$app/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>$app_name</string>
  <key>CFBundleDisplayName</key><string>$app_name</string>
  <key>CFBundleIdentifier</key><string>$bundle_id</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launcher</string>
  <key>CFBundleIconFile</key><string>veille</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
</dict>
</plist>
PLIST

  printf '%s' "$script_body" > "$app/Contents/MacOS/launcher"
  chmod +x "$app/Contents/MacOS/launcher"
  [ -n "$ICNS" ] && cp "$ICNS" "$app/Contents/Resources/veille.icns"

  # Signature ad hoc: evite l'avertissement "application endommagee" et permet
  # a macOS de retenir l'autorisation apres le premier lancement.
  codesign --force --deep --sign - "$app" >/dev/null 2>&1 || true
  echo "Cree        : $app"
}

read -r -d '' WEB_LAUNCHER <<'LAUNCHER' || true
#!/bin/bash
# Ouvre l'interface de consultation. Demarre le serveur s'il ne tourne pas.
set -u
PROJECT="__PROJECT__"
PORT="__PORT__"
VEILLE="$PROJECT/.venv/bin/veille"
URL="http://127.0.0.1:$PORT/"
LOG="$PROJECT/data/logs/web.log"

alert() {
  /usr/bin/osascript -e "display alert \"Veille marches publics\" message \"$1\"" \
    >/dev/null 2>&1 || true
}

if [ ! -x "$VEILLE" ]; then
  alert "Environnement Python introuvable dans $PROJECT/.venv. Ouvrez le Terminal et lancez: cd $PROJECT && python3 -m venv .venv && .venv/bin/pip install -e ."
  exit 1
fi

# Deja demarre: on ouvre simplement le navigateur.
if /usr/bin/curl -fsS --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  /usr/bin/open "$URL"
  exit 0
fi

mkdir -p "$(dirname "$LOG")"

# Ouvre le navigateur des que le serveur repond.
(
  for _ in $(seq 1 60); do
    if /usr/bin/curl -fsS --max-time 1 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
      /usr/bin/open "$URL"
      exit 0
    fi
    sleep 0.5
  done
  alert "Le serveur n'a pas demarre. Journal: $LOG"
) &

# Le serveur devient le processus de l'application: quitter l'application
# (Cmd+Q ou clic droit sur l'icone du Dock) arrete le serveur.
exec "$VEILLE" web --host 127.0.0.1 --port "$PORT" >>"$LOG" 2>&1
LAUNCHER

read -r -d '' RUN_LAUNCHER <<'LAUNCHER' || true
#!/bin/bash
# Lance une collecte immediate et resume le resultat.
set -u
PROJECT="__PROJECT__"
PORT="__PORT__"
VEILLE="$PROJECT/.venv/bin/veille"
LOG="$PROJECT/data/logs/collecte.log"

if [ ! -x "$VEILLE" ]; then
  /usr/bin/osascript -e "display alert \"Veille marches publics\" message \"Environnement Python introuvable dans $PROJECT/.venv\"" >/dev/null 2>&1
  exit 1
fi

mkdir -p "$(dirname "$LOG")"
OUTPUT="$("$VEILLE" run --mode manual 2>&1 | tee -a "$LOG")"

SUMMARY="$(printf '%s\n' "$OUTPUT" | grep -E 'avis dans le digest' | tail -1)"
[ -n "$SUMMARY" ] || SUMMARY="$(printf '%s\n' "$OUTPUT" | tail -3 | tr '\n' ' ')"
# Les guillemets casseraient la commande AppleScript.
SUMMARY="$(printf '%s' "$SUMMARY" | tr '"' "'" | cut -c1-300)"

CHOICE="$(/usr/bin/osascript \
  -e "display dialog \"$SUMMARY\" with title \"Collecte terminee\" buttons {\"Fermer\", \"Ouvrir la liste\"} default button \"Ouvrir la liste\"" \
  2>/dev/null || true)"

case "$CHOICE" in
  *"Ouvrir la liste"*) /usr/bin/open -a "Veille marches publics" ;;
esac
LAUNCHER

WEB_LAUNCHER="${WEB_LAUNCHER//__PROJECT__/$PROJECT_DIR}"
WEB_LAUNCHER="${WEB_LAUNCHER//__PORT__/$PORT}"
RUN_LAUNCHER="${RUN_LAUNCHER//__PROJECT__/$PROJECT_DIR}"
RUN_LAUNCHER="${RUN_LAUNCHER//__PORT__/$PORT}"

mkdir -p "$DEST"
make_app "Veille marches publics" "be.veille.web" "$WEB_LAUNCHER"
make_app "Veille - Collecte" "be.veille.collecte" "$RUN_LAUNCHER"

cat <<EOF

Termine.

  1. Ouvrez le Finder, allez dans $DEST
  2. Double-cliquez sur "Veille marches publics"
  3. Gardez l'icone dans le Dock (clic droit > Options > Garder dans le Dock)

Pour la collecte automatique quotidienne:
  ./scripts/install_launchd.sh
EOF
