#!/usr/bin/env python3
"""Choose how a model's weights are split between RAM and the GPU, and make serve.sh repeat it.

Starts the server once with nothing fixed, reads from its log which weights llama.cpp put where,
stops it, and writes that split to runtime/tuned/MODEL.split. scripts/serve.sh runs this by
itself the first time it serves a model. Run it by hand to choose again, with the GPU as free
as it will be when you use lo-s. Decodes remembered under the old split may then no longer be
what the model answers.

Usage: fix-split.py MODEL.gguf [--port 8089]
"""
import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from los import split  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model")
    parser.add_argument("--port", type=int, default=8089, help="a free port for the trial start")
    opts = parser.parse_args()

    print(f"Choosing a split for {opts.model}: starting it once to see where llama.cpp puts the weights.", flush=True)
    try:
        gpu_layers, tensors, on_gpu = split.choose(opts.model, opts.port)
    except (RuntimeError, ValueError) as error:
        sys.exit(f"No split was chosen: {error}.")
    path = split.write(opts.model, gpu_layers, tensors, on_gpu)
    print(f"{gpu_layers} layers on the GPU ({on_gpu} MiB of weights), {len(tensors)} of their tensors kept in RAM. "
          f"Written to {path.relative_to(ROOT)}.")


if __name__ == "__main__":
    main()
