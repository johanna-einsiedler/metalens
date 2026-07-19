#!/usr/bin/env bash
# Local dev launcher — starts BOTH the web (uvicorn --reload) and the Arq worker
# together, sharing one env. Credit/async extractions are processed by the worker,
# so running only the web makes jobs sit in the queue forever ("extracting…" hangs).
# Ctrl-C stops both. Loads ./.env (gitignored) for secrets if present.
#
#   scripts/dev.sh            # web + worker
#   PAPERLENS_PORT=8001 scripts/dev.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# --- load local secrets / overrides (credit provider key, DB/Redis, …) ----------
if [ -f .env ]; then set -a; . ./.env; set +a; echo "· loaded .env"; fi

HOST="${PAPERLENS_HOST:-127.0.0.1}"
PORT="${PAPERLENS_PORT:-8000}"
REDIS_URL="${PAPERLENS_REDIS_URL:-redis://localhost:6379}"

# --- preflight: Redis must be up or enqueue/worker stalls -----------------------
if command -v redis-cli >/dev/null 2>&1 && ! redis-cli ping >/dev/null 2>&1; then
  echo "⚠️  Redis not responding (${REDIS_URL}) — trying to start it…"
  brew services start redis >/dev/null 2>&1 \
    || echo "   couldn't auto-start; run 'redis-server' in another terminal, then retry."
fi

# --- credit notice: without a model + key, the credit toggle is hidden ----------
if [ -z "${PAPERLENS_CREDIT_MODEL:-}" ]; then
  echo "ℹ️  PAPERLENS_CREDIT_MODEL unset → credit toggle hidden (own-API-key mode only)."
elif [ -z "${PAPERLENS_GOOGLE_KEY:-}${PAPERLENS_OPENAI_KEY:-}${PAPERLENS_ANTHROPIC_KEY:-}" ]; then
  echo "⚠️  PAPERLENS_CREDIT_MODEL=${PAPERLENS_CREDIT_MODEL} but no provider key set —"
  echo "    credit runs will fail. Set PAPERLENS_GOOGLE_KEY / _OPENAI_KEY / _ANTHROPIC_KEY."
else
  echo "· credits: model=${PAPERLENS_CREDIT_MODEL}"
fi

# --- idempotent migrations ------------------------------------------------------
uv run python -c "import paperlens.records as r; r.init_db(r.connect())" \
  && echo "· schema up to date" || echo "⚠️  init_db failed (is Postgres running / DB created?)"

# --- run web + worker; keep the web up and RESTART the worker if it dies ---------
WEB_PID="" WORKER_PID=""
cleanup() {
  echo; echo "stopping…"
  kill "$WORKER_PID" "$WEB_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

start_worker() { uv run arq paperlens.worker.WorkerSettings & WORKER_PID=$!; }

echo "▶ web    : http://${HOST}:${PORT}  (--reload)"
uv run uvicorn paperlens.app:app --reload --host "$HOST" --port "$PORT" & WEB_PID=$!

echo "▶ worker : arq paperlens.worker.WorkerSettings"
start_worker

# The worker doesn't hot-reload and can die on a transient Redis blip. Restart it and keep
# the web running; only a web exit (or Ctrl-C) tears the whole thing down. NOTE: after
# editing worker-side code (worker.py/extract.py/providers.py) you still must restart
# dev.sh — auto-restart only covers crashes, not code changes.
while kill -0 "$WEB_PID" 2>/dev/null; do
  if ! kill -0 "$WORKER_PID" 2>/dev/null; then
    echo "⚠️  worker exited — restarting in 2s (Ctrl-C to quit)…"; sleep 2; start_worker
  fi
  sleep 1
done
echo "web exited — shutting down."
