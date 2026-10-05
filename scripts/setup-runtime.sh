#!/usr/bin/env bash
# Fetch the pinned llama.cpp CUDA build and the student models into runtime/.
# Nothing is installed system-wide; delete runtime/ to undo. Downloads resume if interrupted.
# Usage: scripts/setup-runtime.sh [llama] [small] [big]   (default: all three, in that order)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RT="$ROOT/runtime"
BUILD=b11146                      # the nightly that llama.cpp v0.5.0 points at
CUDA=13.4
GH="https://github.com/ggml-org/llama.cpp/releases/download/$BUILD"
HF="https://huggingface.co"

fetch() {  # url dest
    mkdir -p "$(dirname "$2")"
    [ -f "$2.done" ] && return 0
    curl -L --fail --retry 20 --retry-delay 5 --retry-all-errors -C - -sS -o "$2" "$1"
    touch "$2.done"
}

llama() {
    for a in "llama-$BUILD-bin-ubuntu-cuda-$CUDA-x64" "cudart-llama-$BUILD-bin-ubuntu-cuda-$CUDA-x64"; do
        fetch "$GH/$a.tar.gz" "$RT/dl/$a.tar.gz"
        tar -xzf "$RT/dl/$a.tar.gz" -C "$RT/llama.cpp"
    done
}
small() { fetch "$HF/LiquidAI/LFM2.5-8B-A1B-GGUF/resolve/main/LFM2.5-8B-A1B-Q4_K_M.gguf" "$RT/models/LFM2.5-8B-A1B-Q4_K_M.gguf"; }
big()   { fetch "$HF/google/gemma-4-26B-A4B-it-qat-q4_0-gguf/resolve/main/gemma-4-26B_q4_0-it.gguf" "$RT/models/gemma-4-26B-A4B-it-qat-q4_0.gguf"; }

[ $# -eq 0 ] && set -- llama small big
for step in "$@"; do "$step"; echo "$(date +%T) done: $step"; done
