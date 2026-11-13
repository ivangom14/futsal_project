#!/usr/bin/env bash
# Actualización incremental de los datos de la RFFM con la cola de ingesta (un solo comando).
#
#   scripts/update_rffm.sh            # hasta 900 peticiones (≈45 min a 1 petición cada 3 s)
#   scripts/update_rffm.sh 200        # otro tope de peticiones
#
# Pasos: recupera trabajos colgados -> planifica según las fechas reales de los partidos (sin descubrir competiciones
# nuevas) -> descarga lo que toca (partidos, resultados, actas, clasificación, goleadores) y para cuando no queda nada.
# Requisitos: entorno Python del proyecto, DATABASE_URL (en el entorno o en .env) y FUTSAL_RFFM_NETWORK_AUTHORIZED=1
# (en el entorno o en .env): sin esta autorización no se hace ninguna petición a la RFFM.
# Si ya hay otra actualización en curso, sale sin hacer nada (código 0).
set -euo pipefail

cd "$(dirname "$0")/.."
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
PY="${PYTHON:-.venv/bin/python}"
[ -x "$PY" ] || PY=python3
MAX_REQUESTS="${1:-900}"

mkdir -p data logs
exec 9>data/.update_rffm.lock
if ! flock -n 9; then
  echo "$(date '+%F %T') otra actualización está en curso: se omite esta"
  exit 0
fi

echo "$(date '+%F %T') inicio (tope ${MAX_REQUESTS} peticiones)"
"$PY" -m futsal.cli ingest recover
"$PY" -m futsal.cli ingest tick --skip-catalog
status=0
"$PY" -m futsal.cli ingest work --allow-network --until-empty --max-requests "$MAX_REQUESTS" || status=$?
"$PY" -m futsal.cli ingest status
echo "$(date '+%F %T') fin (código ${status})"
exit "$status"
