#!/usr/bin/env bash
# Measure agent cold-start latency: destroy the agent's worker container, then
# time how long GET /agent/resolve takes to return a healthy worker.
# Usage: measure-coldstart.sh <agent_id> [label]
set -u
AGENT_ID="${1:?usage: measure-coldstart.sh <agent_id> [label]}"
LABEL="${2:-run}"
BASE="http://localhost:18790/api/v1"

# Force a clean cold start
curl -s -X POST "$BASE/agent/container/destroy" \
  -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$AGENT_ID\"}" >/dev/null 2>&1
sleep 1

T0=$(date +%s.%N)
HTTP=$(curl -s -o /tmp/coldstart_resp.json -w '%{http_code}' --max-time 180 \
  "$BASE/agent/resolve?agent_id=$AGENT_ID")
T1=$(date +%s.%N)
WALL=$(echo "$T1 - $T0" | bc)

# Pull the manager's own timing + which path was taken from the logs
HEALTHY=$(docker logs bond-bond-1 --since 200s 2>&1 | grep -E "Worker healthy for agent $AGENT_ID in" | tail -1 | grep -oE "in [0-9.]+s" | tail -1)
CREATED=$(docker logs bond-bond-1 --since 200s 2>&1 | grep -cE "Created worker container.*$AGENT_ID")
RECOVERED=$(docker logs bond-bond-1 --since 200s 2>&1 | grep -cE "Recovered running container.*$AGENT_ID")
TIMEOUT=$(docker logs bond-bond-1 --since 200s 2>&1 | grep -cE "Health check timeout for agent $AGENT_ID")

printf '[%s] http=%s wall=%.1fs worker_healthy=%s created=%s recovered=%s recovery_timeouts=%s\n' \
  "$LABEL" "$HTTP" "$WALL" "${HEALTHY:-n/a}" "$CREATED" "$RECOVERED" "$TIMEOUT"
