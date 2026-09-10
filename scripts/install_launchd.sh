#!/usr/bin/env bash
# Installe la collecte quotidienne automatique sur macOS (launchd).
#
# launchd est l'equivalent macOS de cron. Avantage ici: si le Mac dort a
# l'heure prevue, la tache est lancee au reveil au lieu d'etre sautee.
#
# Usage:   ./scripts/install_launchd.sh [--at 07:00]
#          ./scripts/install_launchd.sh --uninstall
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="be.veille.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
AT=""
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --at) AT="$2"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Option inconnue: $1" >&2; exit 1 ;;
  esac
done

if [ "$(uname -s)" != "Darwin" ]; then
  echo "launchd n'existe que sur macOS. Sur Linux, utilisez cron ou systemd" >&2
  echo "(voir scripts/run-daily.sh et scripts/veille.timer)." >&2
  exit 1
fi

unload() {
  launchctl bootout "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \
    || launchctl unload "$PLIST" >/dev/null 2>&1 || true
}

if [ "$UNINSTALL" = "1" ]; then
  unload
  rm -f "$PLIST"
  echo "Collecte quotidienne desinstallee."
  exit 0
fi

# Heure: --at, sinon celle de la configuration, sinon 07:00
if [ -z "$AT" ]; then
  AT="$(awk '/^schedule:/{f=1;next} f&&/^  daily_at:/{gsub(/"/,"",$2);print $2;exit} f&&/^[^ ]/{f=0}' \
    "$PROJECT_DIR/config/config.yaml" 2>/dev/null || true)"
fi
AT="${AT:-07:00}"
HOUR="${AT%%:*}"
MINUTE="${AT##*:}"
HOUR="$((10#$HOUR))"
MINUTE="$((10#${MINUTE:-0}))"

VEILLE="$PROJECT_DIR/.venv/bin/veille"
if [ ! -x "$VEILLE" ]; then
  echo "Introuvable: $VEILLE" >&2
  echo "Installez d'abord le projet: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/data/logs"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VEILLE</string>
    <string>run</string>
    <string>--mode</string>
    <string>daily</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>$HOUR</integer>
    <key>Minute</key><integer>$MINUTE</integer>
  </dict>
  <!-- Rattrape l'execution si le Mac dormait a l'heure prevue -->
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>$PROJECT_DIR/data/logs/launchd.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/data/logs/launchd.log</string>
  <key>ProcessType</key><string>Background</string>
</dict>
</plist>
PLISTEOF

unload
if launchctl bootstrap "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 \
   || launchctl load "$PLIST" >/dev/null 2>&1; then
  printf 'Collecte quotidienne installee: tous les jours a %02d:%02d\n' "$HOUR" "$MINUTE"
else
  echo "Le fichier a ete ecrit ($PLIST) mais launchctl a refuse de le charger." >&2
  echo "Reessayez apres une deconnexion/reconnexion de session." >&2
  exit 1
fi

cat <<EOF

  Verifier    : launchctl list | grep $LABEL
  Lancer maintenant : launchctl kickstart -k gui/\$(id -u)/$LABEL
  Journal     : $PROJECT_DIR/data/logs/launchd.log
  Desinstaller: ./scripts/install_launchd.sh --uninstall
EOF
