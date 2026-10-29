#!/bin/sh
# Start a worker and the API on a fresh fixture library: e2e/serve.sh PORT [PASSWORD]
set -e
port=$1
folder=$(mktemp -d)
python=${PYTHON:-../.venv/bin/python}
mkdir -p test-results
environment=$("$python" ../tests/e2e_library.py "$folder")
export $environment
export SUBTITLE_GUARD_UI_PORT="$port" BAZARR_CONFIG="$folder/none.yaml" SUBTITLE_GUARD_UI_PASSWORD="${2:-}"
echo "$folder" > "test-results/library-$port.txt"
trap 'kill 0' EXIT INT TERM
"$python" -m subtitle_guard worker --workers 1 > "$folder/worker.log" 2>&1 &
"$python" -m subtitle_guard ui > "$folder/ui.log" 2>&1 &
wait
