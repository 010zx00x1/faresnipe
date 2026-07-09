#!/usr/bin/env bash
# Smoke end-to-end de faresnipe. Valida: doctor, scan, recent, deals, stats, dashboard.
# No toca Google Flights real: usa --dry-run con el provider mock. Pensado para CI.
#
# Uso: ./scripts/smoke.sh [python_bin]
# Salida: 0 si todo pasa, !=0 si algo falla.

set -euo pipefail

PY=${1:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

DB="data/smoke.sqlite3"
LOG_PREFIX="[smoke]"

echo "$LOG_PREFIX usando python: $($PY --version 2>&1)"

# 1) Doctor (no falla aunque falten deps opcionales, solo se reportan)
echo "$LOG_PREFIX 1) doctor"
"$PY" -m faresnipe doctor --config config/routes.example.toml | tee /tmp/smoke-doctor.txt
grep -q "Configuracion" /tmp/smoke-doctor.txt
echo "$LOG_PREFIX   doctor OK"

# 2) Scan con mock (3 busquedas)
echo "$LOG_PREFIX 2) scan --dry-run"
rm -f "$DB"
FARESNIPE_DATABASE="$DB" "$PY" -m faresnipe --config config/routes.example.toml --dry-run --limit-searches 3

# 3) recent / deals / stats deben devolver filas
echo "$LOG_PREFIX 3) recent"
ROWS=$("$PY" -m faresnipe --config config/routes.example.toml recent --limit 5 | tail -n +2 | wc -l)
test "$ROWS" -ge 1
echo "$LOG_PREFIX   recent: $ROWS filas"

echo "$LOG_PREFIX 4) deals"
"$PY" -m faresnipe --config config/routes.example.toml deals --limit 5 | head -3

echo "$LOG_PREFIX 5) stats"
"$PY" -m faresnipe --config config/routes.example.toml stats | head -3

# 6) Dashboard API (bind en puerto aleatorio, 3 segundos)
echo "$LOG_PREFIX 6) dashboard API"
PORT=$((9000 + RANDOM % 1000))
FARESNIPE_DATABASE="$DB" "$PY" -m faresnipe dashboard --port "$PORT" --host 127.0.0.1 >/tmp/smoke-dash.log 2>&1 &
DASH_PID=$!
trap 'kill $DASH_PID 2>/dev/null || true' EXIT
sleep 2

curl -fsS "http://127.0.0.1:$PORT/api/summary" >/tmp/smoke-summary.json
curl -fsS "http://127.0.0.1:$PORT/api/routes"    >/tmp/smoke-routes.json
curl -fsS "http://127.0.0.1:$PORT/api/opportunities" >/tmp/smoke-opp.json
curl -fsS -o /dev/null -w "  /static/app.js   -> %{http_code}\n" "http://127.0.0.1:$PORT/static/app.js"
curl -fsS -o /dev/null -w "  /static/style.css -> %{http_code}\n" "http://127.0.0.1:$PORT/static/style.css"

# 7) Disparar un scan chico por la API
echo "$LOG_PREFIX 7) POST /api/scan"
SCAN=$(curl -fsS -X POST "http://127.0.0.1:$PORT/api/scan" \
    -H 'Content-Type: application/json' \
    -d '{"limit_searches": 1, "providers": ["mock"]}')
echo "$SCAN"
echo "$SCAN" | grep -q '"searches"'
echo "$SCAN" | grep -q '"status"'

kill $DASH_PID 2>/dev/null || true
trap - EXIT
wait $DASH_PID 2>/dev/null || true

echo
echo "$LOG_PREFIX TODOS LOS CHECKS PASARON"
