#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
profile=${1:-dashboard}
if [[ $# -gt 1 ]]; then echo "Usage: $0 [dashboard|cpu|dev]" >&2; exit 2; fi
case "$profile" in
    dashboard) package='.' ;;
    cpu) package='.[cpu]' ;;
    dev) package='.[dev]' ;;
    -h|--help) echo "Usage: $0 [dashboard|cpu|dev]"; exit 0 ;;
    *) echo "Unknown setup profile: $profile" >&2; exit 2 ;;
esac
python3 -m venv .venv
.venv/bin/python -m pip install -e "$package"
mkdir -p runs models
echo "Installed $profile dependencies. Model downloads are a separate step."
echo "First-time configuration: .venv/bin/python scripts/control_camera.py configure"
