#!/usr/bin/env bash
# Start llama-server on a model in runtime/models. Extra arguments go straight to llama-server.
# Usage: scripts/serve.sh MODEL.gguf [--n-cpu-moe N ...]
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

# One slot and no thinking: dispatch is a single short answer per request.
# Only pages served from this machine may read the server's answers in a browser.
exec "$LLAMA/llama-server" -m "$ROOT/runtime/models/$model" --host 127.0.0.1 --port "${PORT:-8080}" \
    --ctx-size 8192 --parallel 1 --reasoning-budget 0 --cors-origins localhost \
    ${tuned[@]+"${tuned[@]}"} "$@"
