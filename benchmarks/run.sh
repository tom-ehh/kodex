#!/usr/bin/env bash
# Runs every benchmark in benchmarks/results/ and writes RESULTS.md. Each step logs
# DONE/FAILED to steps.txt and the run continues. Run from the repository root with the
# router up (docker compose up router) and COI_EDENAI_API_KEY set.
set -u
D=${OUT:-benchmarks/results}
BASAL=${BASAL:-http://127.0.0.1:8001}
mkdir -p "$D"; : > "$D/steps.txt"
step() {  # name, command...
  local name=$1; shift
  echo "=== $(date -Is) START $name"
  if "$@"; then echo "$name DONE $(date -Is)" >> "$D/steps.txt"
  else echo "$name FAILED (exit $?) $(date -Is)" >> "$D/steps.txt"; fi
}
C=(--cache .bench-cache)
step skills uv run python tools/bench_morpheus.py "${C[@]}" --max-usd 5 --modes off,on \
  --title "Seed skills v2: OFF vs ON" --out "$D/skills"
if curl -sf -m10 "$BASAL/health" >/dev/null; then
  step routing-routed uv run python tools/bench_morpheus.py "${C[@]}" --max-usd 3 --modes off \
    --router-url "$BASAL" --title "Routing: basal-routed (skills off)" --out "$D/routing-routed"
  step routing-hard uv run python tools/bench_morpheus.py "${C[@]}" --max-usd 3 --modes off \
    --router-url "$BASAL" --force-tier hard --title "Routing: everything forced to hard (skills off)" \
    --out "$D/routing-hard"
  step morpheus-wake uv run python tools/bench_morpheus_wake.py "${C[@]}" --max-usd 5 \
    --basal "$BASAL" --out "$D/morpheus-wake"
  step morpheus-sleep uv run python tools/bench_morpheus_sleep.py "${C[@]}" --max-usd 5 \
    --basal "$BASAL" --wake "$D/morpheus-wake/results.json" --out "$D/morpheus-sleep"
else
  echo "routing+morpheus FAILED: router not reachable at $BASAL $(date -Is)" >> "$D/steps.txt"
fi
step summary uv run python benchmarks/summarise.py "$D"
