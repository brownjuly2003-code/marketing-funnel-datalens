#!/usr/bin/env bash
# Unattended DataLens OSS stand check for the marketing funnel.
# Prepared from documentation and source code (docs/runs/2026-11-19-stand-prep.md);
# first passing run: docs/runs/2026-11-20-stand.md.
#
# Usage on any Linux machine with Docker (compose v2) and Python 3.11+:
#   bash stand/run.sh
# Result: stand/out/SUMMARY.txt and the files next to it. Exit code 0 only when the numbers read
# from DataLens equal the reference report, every chart run returns table rows and every
# screenshot page answers with HTTP status below 400 (the dashboard also shows its loaded class).
# With SKIP_SCREENSHOTS=1 the code 0 covers numbers and charts only; the "overall:" line of
# SUMMARY.txt says which of the two successes it is.
#   1 numbers differ (table "ожидалось / получено" in SUMMARY.txt and comparison.txt)
#   2 a DataLens API call or chart run failed (no table rows in the /api/run answer)
#   3 the host is too small (disk) or a tool is missing
#   4 screenshots failed
#   5 the generated snapshot (inputs.json or report.json) differs from the reference SHA-256
#   6 the stand did not start or the marts did not load (see last_step and compose_logs.txt)
#   any other code: an unexpected command failure, see last_step and run.log
# Environment: MIN_DISK_GB (default 10), SKIP_SCREENSHOTS=1, PYTHON (default python3.11 or python3),
#   KEEP_STAND=1 (debugging only: skip the final "docker compose down -v", the stand stays up).
set -euo pipefail

STAND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$STAND_DIR")"
OUT="$STAND_DIR/out"
ENV_FILE="$STAND_DIR/.env"
REFERENCE="$ROOT/docs/runs/2026-10-31-synthetic"
DATABASE="marketing_funnel"
MIN_DISK_GB="${MIN_DISK_GB:-10}"
PYTHON="${PYTHON:-$(command -v python3.11 || command -v python3 || true)}"
COMPOSE=(docker compose -f "$STAND_DIR/docker-compose.yml" --env-file "$ENV_FILE")

rm -rf "$OUT"
mkdir -p "$OUT"
exec > >(tee "$OUT/run.log") 2>&1

STEP="start"
declare -A RESULT=()
STARTED_UP=0

log() { printf '\n[%s] %s\n' "$(date -u +%H:%M:%S)" "$*"; }

finish() {
  local rc=$?
  set +e
  if [ "$STARTED_UP" = 1 ]; then
    log "collect: compose ps, logs"
    "${COMPOSE[@]}" ps -a > "$OUT/compose_ps.txt" 2>&1
    "${COMPOSE[@]}" logs --no-color --timestamps > "$OUT/compose_logs.txt" 2>&1
  fi
  {
    echo "DataLens OSS stand check (marketing-funnel-datalens)"
    echo "finished_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "commit: $(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    if [ "$rc" = 0 ] && [ "${SKIP_SCREENSHOTS:-0}" = 1 ]; then
      echo "overall: PASS, numbers and charts only, screenshots skipped (SKIP_SCREENSHOTS=1)"
    elif [ "$rc" = 0 ]; then
      echo "overall: PASS, numbers match, charts return rows, screenshot pages loaded"
    else
      echo "overall: FAIL"
    fi
    echo "exit_code: $rc"
    echo "last_step: $STEP"
    for key in host compose_up snapshot load datalens screenshots; do
      echo "step_$key: ${RESULT[$key]:-not reached}"
    done
    echo
    echo "== host =="
    cat "$OUT/host.txt" 2>/dev/null
    echo
    echo "== comparison (DataLens data API vs docs/runs/2026-10-31-synthetic/report.json) =="
    cat "$OUT/comparison.txt" 2>/dev/null || echo "not produced"
    echo
    echo "== screenshots =="
    ls -1 "$OUT/screenshots" 2>/dev/null || echo "none"
    echo
    echo "== docker stats --no-stream =="
    cat "$OUT/docker_stats.txt" 2>/dev/null || echo "not collected"
  } > "$OUT/SUMMARY.txt"
  if [ "$STARTED_UP" = 1 ] && [ "${KEEP_STAND:-0}" = 1 ]; then
    log "teardown skipped (KEEP_STAND=1): stop the stand with docker compose down -v"
  elif [ "$STARTED_UP" = 1 ]; then
    log "teardown: docker compose down -v"
    "${COMPOSE[@]}" down -v --remove-orphans
  fi
  log "exit code $rc; summary: $OUT/SUMMARY.txt"
  exit "$rc"
}
trap finish EXIT

STEP="host"
log "1. host resources"
for tool in docker git; do
  command -v "$tool" > /dev/null || { echo "STOP: $tool is not installed"; exit 3; }
done
docker compose version > /dev/null || { echo "STOP: docker compose v2 is required"; exit 3; }
if [ -z "$PYTHON" ] || ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
  echo "STOP: Python 3.11+ is required (set PYTHON=...)"
  exit 3
fi
DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2> /dev/null || echo /)"
[ -d "$DOCKER_ROOT" ] || DOCKER_ROOT="/"
{
  echo "nproc: $(nproc)"
  echo "--- free -g"
  free -g
  echo "--- df -h"
  df -h
  echo "--- docker root: $DOCKER_ROOT"
  docker version --format 'docker {{.Server.Version}}'
  docker compose version
  "$PYTHON" --version
} > "$OUT/host.txt" 2>&1
cat "$OUT/host.txt"
FREE_GB="$(df -BG --output=avail "$DOCKER_ROOT" | tail -1 | tr -dc '0-9')"
if [ "${FREE_GB:-0}" -lt "$MIN_DISK_GB" ]; then
  echo "STOP: ${FREE_GB:-0} GB free under $DOCKER_ROOT, need at least $MIN_DISK_GB GB"
  echo "      (images are about 1.2 GB compressed, see docs/runs/2026-11-19-stand-prep.md)"
  RESULT[host]="FAIL disk ${FREE_GB:-0} GB < $MIN_DISK_GB GB"
  exit 3
fi
MEM_GB="$(free -g | awk '/^Mem:/ {print $2}')"
if [ "${MEM_GB:-0}" -lt 8 ]; then
  echo "WARNING: ${MEM_GB:-0} GB RAM; DataLens README asks for 4 GB, ClickHouse needs more on top"
fi
RESULT[host]="ok (cpu $(nproc), ram ${MEM_GB} GB, disk free ${FREE_GB} GB)"

STEP="compose_up"
log "2. docker compose up -d --wait"
if [ ! -f "$ENV_FILE" ]; then
  echo "creating $ENV_FILE with random secrets"
  (umask 077 && "$PYTHON" - "$ENV_FILE" << 'PY'
import base64, secrets, sys
token = lambda: secrets.token_hex(16)
values = {
    "POSTGRES_PASSWORD": token(),
    "US_MASTER_TOKEN": token(),
    "AUTH_MASTER_TOKEN": token(),
    "CONTROL_API_CRYPTO_KEY": base64.b64encode(token().encode()).decode(),
    "CLICKHOUSE_DATALENS_PASSWORD": token(),
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    handle.writelines(f"{key}={value}\n" for key, value in values.items())
PY
  )
fi
set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a
STARTED_UP=1
if ! "${COMPOSE[@]}" pull --quiet || ! "${COMPOSE[@]}" up -d --wait --wait-timeout 900; then
  RESULT[compose_up]="FAIL"
  exit 6
fi
"${COMPOSE[@]}" ps
docker stats --no-stream > "$OUT/docker_stats_after_up.txt" || true
RESULT[compose_up]="ok"

STEP="snapshot"
log "3. generate the synthetic snapshot and load the marts into ClickHouse"
if ! PYTHONPATH="$ROOT/src" "$PYTHON" -m marketing_funnel --out "$OUT/snapshot" --size 120 \
  --seed 20261031; then
  RESULT[snapshot]="FAIL generator"
  exit 5
fi
# Both files byte for byte: the loader reads inputs.json, so equal reports alone are not enough.
if ! PYTHONPATH="$ROOT/src" "$PYTHON" -m marketing_funnel.stand_check "$OUT/snapshot" \
  "$REFERENCE"; then
  echo "STOP: the generated snapshot differs from the SHA-256 in $REFERENCE/manifest.json"
  RESULT[snapshot]="FAIL differs from the reference"
  exit 5
fi
RESULT[snapshot]="ok (seed 20261031, size 120, inputs.json and report.json match the manifest)"
STEP="load"
CLICKHOUSE_CONTAINER="$("${COMPOSE[@]}" ps -q clickhouse)"
if ! PYTHONPATH="$ROOT/src" "$PYTHON" "$ROOT/scripts/load_clickhouse.py" \
  --input "$OUT/snapshot/inputs.json" \
  --database "$DATABASE" --container "$CLICKHOUSE_CONTAINER"; then
  RESULT[load]="FAIL"
  exit 6
fi
RESULT[load]="ok (database $DATABASE)"

STEP="datalens"
log "4-5. create connection, datasets, charts, dashboard; read numbers from DataLens"
set +e
"$PYTHON" "$STAND_DIR/datalens_stand.py" --out "$OUT" --database "$DATABASE" \
  --report "$REFERENCE/report.json"
CHECK_RC=$?
set -e
case "$CHECK_RC" in
  0) RESULT[datalens]="ok (numbers match, charts build)" ;;
  1) RESULT[datalens]="FAIL numbers differ" ;;
  *) RESULT[datalens]="FAIL API or chart run (code $CHECK_RC)" ;;
esac

STEP="screenshots"
log "6. screenshots, docker stats"
SHOT_RC=0
if [ "${SKIP_SCREENSHOTS:-0}" = 1 ]; then
  RESULT[screenshots]="skipped (SKIP_SCREENSHOTS=1)"
elif [ ! -f "$OUT/objects.json" ]; then
  RESULT[screenshots]="skipped (no objects created)"
  SHOT_RC=4
else
  set +e
  (
    set -e
    [ -x "$STAND_DIR/.venv/bin/python" ] || "$PYTHON" -m venv "$STAND_DIR/.venv"
    "$STAND_DIR/.venv/bin/python" -m pip install --quiet -r "$STAND_DIR/requirements.txt"
    "$STAND_DIR/.venv/bin/python" -m playwright install --with-deps chromium
    "$STAND_DIR/.venv/bin/python" "$STAND_DIR/screenshots.py" --out "$OUT"
  )
  SHOT_RC=$?
  set -e
  if [ "$SHOT_RC" = 0 ]; then RESULT[screenshots]="ok"; else RESULT[screenshots]="FAIL"; SHOT_RC=4; fi
fi
docker stats --no-stream > "$OUT/docker_stats.txt" || true

STEP="done"
if [ "$CHECK_RC" != 0 ]; then
  exit "$CHECK_RC"
fi
exit "$SHOT_RC"
