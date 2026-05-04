#!/usr/bin/env bash
set -uo pipefail

PID_DIR="/tmp/visual-ui-test"

stopped=0
for pidfile in "$PID_DIR"/*.pid; do
    [ -f "$pidfile" ] || continue
    service="$(basename "$pidfile" .pid)"
    pid="$(cat "$pidfile")"

    if kill -0 "$pid" 2>/dev/null; then
        # Kill the process group to catch child processes
        kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null
        echo "Stopped $service (PID $pid)"
        ((stopped++))
    else
        echo "$service (PID $pid) was already dead"
    fi
    rm -f "$pidfile"
done

if [ "$stopped" -eq 0 ]; then
    echo "No running services found."
fi

echo "Dev environment stopped."
