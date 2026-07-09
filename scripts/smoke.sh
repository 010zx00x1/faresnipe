#!/usr/bin/env bash
# End-to-end smoke test for faresnipe. Validates: init, scan, and dashboard API.
# Does not touch real Google Flights: uses --dry-run with the mock provider. Designed for CI.
#
# Usage: ./scripts/smoke.sh [python_bin]
# Output: 0 if everything passes, non-zero if something fails.

set -euo pipefail

PY=${1:-python3}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

DB="data/smoke.sqlite3"
SMOKE_CONFIG="/tmp/faresnipe-smoke.toml"
LOG_PREFIX="[smoke]"

echo "$LOG_PREFIX using python: $($PY --version 2>&1)"

# 1) Init creates a translated example configuration.
echo "$LOG_PREFIX 1) init"
rm -f "$SMOKE_CONFIG"
"$PY" -m faresnipe --config "$SMOKE_CONFIG" init | tee /tmp/smoke-init.txt
grep -q "Created" /tmp/smoke-init.txt
grep -q "Example configuration" "$SMOKE_CONFIG"
echo "$LOG_PREFIX   init OK"

# 2) Scan with mock (3 searches)
echo "$LOG_PREFIX 2) scan --dry-run"
rm -f "$DB"
FARESNIPE_DATABASE="$DB" "$PY" -m faresnipe --config config/routes.example.toml run --dry-run --limit 3

# 3) Dashboard API (bind to a random port, 3 seconds)
echo "$LOG_PREFIX 3) dashboard API"
PORT=$((9000 + RANDOM % 1000))
FARESNIPE_DATABASE="$DB" "$PY" -m faresnipe --config config/routes.example.toml serve --port "$PORT" --host 127.0.0.1 >/tmp/smoke-dash.log 2>&1 &
DASH_PID=$!
trap 'kill $DASH_PID 2>/dev/null || true' EXIT
sleep 2

curl -fsS "http://127.0.0.1:$PORT/api/summary" >/tmp/smoke-summary.json
curl -fsS "http://127.0.0.1:$PORT/api/routes"    >/tmp/smoke-routes.json
curl -fsS "http://127.0.0.1:$PORT/api/opportunities" >/tmp/smoke-opp.json
curl -fsS "http://127.0.0.1:$PORT/api/stats" >/tmp/smoke-stats.json
curl -fsS -o /dev/null -w "  /static/app.js   -> %{http_code}\n" "http://127.0.0.1:$PORT/static/app.js"
curl -fsS -o /dev/null -w "  /static/style.css -> %{http_code}\n" "http://127.0.0.1:$PORT/static/style.css"

# 4) Trigger a small scan through the API.
echo "$LOG_PREFIX 4) POST /api/scan"
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
echo "$LOG_PREFIX ALL CHECKS PASSED"
