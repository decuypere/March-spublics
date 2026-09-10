#!/usr/bin/env bash
# Execution quotidienne de la veille. A appeler depuis cron.
# Exemple de ligne crontab (tous les jours a 07h00):
#   0 7 * * * /chemin/vers/March-spublics/scripts/run-daily.sh >> /chemin/vers/March-spublics/data/logs/cron.log 2>&1
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"

if [ -x ".venv/bin/veille" ]; then
  VEILLE=".venv/bin/veille"
else
  VEILLE="$(command -v veille)"
fi

mkdir -p data/logs
exec "$VEILLE" run --mode daily
