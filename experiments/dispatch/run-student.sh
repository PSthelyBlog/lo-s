#!/usr/bin/env bash
# Serve one model, record its dispatches at every table size, then stop the server.
# Usage: run-student.sh TAG MODEL.gguf [llama-server arguments]
# Arguments for dispatch.py, such as --examples TEACHER_TAG, go in DISPATCH_ARGS.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
tag="$1"; model="$2"; shift 2
log="$HERE/results/$tag-server.log"

# LOS_NO_SPLIT: the placement is the caller's to choose here, not the one serve.sh would repeat.
LOS_NO_SPLIT=1 "$HERE/../../scripts/serve.sh" "$model" "$@" > "$log" 2>&1 &
server=$!
trap 'kill $server 2>/dev/null; wait $server 2>/dev/null || true' EXIT
until curl -sf http://127.0.0.1:${PORT:-8080}/health > /dev/null; do
    kill -0 $server 2>/dev/null || { echo "server exited, see $log"; exit 1; }
    sleep 1
done

nvidia-smi --query-gpu=memory.used --format=csv,noheader > "$HERE/results/$tag-vram.txt"
python3 "$HERE/dispatch.py" openai "$tag" --model "$model" --url "http://127.0.0.1:${PORT:-8080}/v1" ${DISPATCH_ARGS:-}
