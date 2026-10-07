#!/usr/bin/env bash
# Dashboard lifecycle only; camera lifecycle belongs to control_camera.py.
set -euo pipefail
cd "$(dirname "$0")/.."
action=${1:-help}
if [[ $# -gt 0 ]]; then shift; fi
mkdir -p runs
alive() {
    [[ -f runs/dashboard.pid ]] || return 1
    pid=$(cat runs/dashboard.pid)
    [[ "$pid" =~ ^[0-9]+$ && -r /proc/$pid/cmdline ]] || return 1
    grep -q 'construction_safety.dashboard' /proc/"$pid"/cmdline
}
case "$action" in
    start)
        [[ -f configs/dashboard.json ]] || { echo "Run python3 scripts/control_camera.py configure first" >&2; exit 1; }
        if alive; then echo "Dashboard already running: PID $pid"; exit 0; fi
        [[ -x .venv/bin/python ]] || { echo 'Run bash scripts/setup.sh first' >&2; exit 1; }
        setsid nohup .venv/bin/python -u -m construction_safety.dashboard "$@" > runs/dashboard.log 2>&1 < /dev/null &
        echo $! > runs/dashboard.pid
        sleep 1
        if ! alive; then echo 'Dashboard failed to start; see runs/dashboard.log' >&2; exit 1; fi
        echo "Dashboard started: PID $pid. Default URL: http://localhost:8765"
        ;;
    stop)
        if ! alive; then echo 'Dashboard is stopped'; exit 0; fi
        kill -TERM "$pid"
        for _ in {1..50}; do
            if ! alive; then rm -f runs/dashboard.pid; echo 'Dashboard stopped'; exit 0; fi
            sleep 0.2
        done
        echo 'Dashboard has not stopped; inspect runs/dashboard.log' >&2
        exit 1
        ;;
    status)
        if alive; then echo "Dashboard running: PID $pid"; else echo 'Dashboard is stopped'; fi
        ;;
    help|-h|--help) echo "Usage: $0 {start [--host HOST --port PORT]|stop|status}" ;;
    *) echo "Unknown action: $action" >&2; exit 2 ;;
esac
