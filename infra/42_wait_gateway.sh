#!/usr/bin/env bash
# Phase 2: poll t1 through Unity Gateway every 60 s (max 20 min) until accepted; exit 1 on timeout.
set -euo pipefail
cd "$(dirname "$0")/.."
for i in $(seq 1 20); do
  OUT=$(uv run python -m client.lab t1 --inbound entra --tag phase2 2>&1 || true)
  OUTCOME=$(printf '%s' "$OUT" | python3 -c "import json,sys; s=sys.stdin.read(); i=s.find('{'); print(json.loads(s[i:]).get('outcome','?') if i >= 0 else 'no-json')" 2>/dev/null || echo parse-error)
  echo "$(date -u +%H:%M:%SZ) attempt $i outcome=$OUTCOME"
  [ "$OUTCOME" = "accepted" ] && exit 0
  sleep 60
done
echo "gateway did not reach the private MCP server within 20 min"; exit 1
