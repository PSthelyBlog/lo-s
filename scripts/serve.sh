#!/usr/bin/env bash
# Start llama-server on a model in runtime/models. Extra arguments go straight to llama-server.
# Usage: scripts/serve.sh MODEL.gguf [--threads N ...]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LLAMA="$(echo "$ROOT"/runtime/llama.cpp/llama-b*)"
export LD_LIBRARY_PATH="$LLAMA:$(echo "$ROOT"/runtime/llama.cpp/cudart-*)"
model="$1"; shift

# Settings found by scripts/tune-server.py for this model on this machine, if any.
tuned=()
if [ -z "${LOS_NO_TUNED:-}" ] && [ -f "$ROOT/runtime/tuned/$model.args" ]; then
    read -r -a tuned < "$ROOT/runtime/tuned/$model.args"
    echo "Using tuned settings: ${tuned[*]}" >&2
fi

# Which weights sit on the GPU and which in RAM. Left alone, llama.cpp decides that at every start
# from the GPU memory free at that moment, and another split gives other answers. So the split is
# chosen once, by scripts/fix-split.py, and repeated. Set LOS_NO_SPLIT to try another placement.
split=()
if [ -z "${LOS_NO_SPLIT:-}" ]; then
    chosen="$ROOT/runtime/tuned/$model.split"
    [ -f "$chosen" ] || "$ROOT/scripts/fix-split.py" "$model" >&2
    read -r -a split < <(grep -v '^#' "$chosen")
    echo "Using the split in runtime/tuned/$model.split. If the server stops for lack of GPU memory," \
         "free some, or delete that file to choose a new split and with it new answers." >&2
fi

# One slot and no thinking: dispatch is a single short answer per request.
# Only pages served from this machine may read the server's answers in a browser.
exec "$LLAMA/llama-server" -m "$ROOT/runtime/models/$model" --host 127.0.0.1 --port "${PORT:-8080}" \
    --ctx-size 8192 --parallel 1 --reasoning-budget 0 --cors-origins localhost \
    ${tuned[@]+"${tuned[@]}"} ${split[@]+"${split[@]}"} "$@"
