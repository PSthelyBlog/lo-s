#!/usr/bin/env python3
"""Measure a mixture-of-experts model as more of its expert layers move from the GPU into RAM.

For each N, serves the model with --n-cpu-moe N (expert weights of the first N layers stay in
RAM), dispatches a few lines against the 200-command table and prints one row. "auto" lets
llama.cpp choose the placement itself.

Usage: placement-sweep.py MODEL.gguf N [N ...]
"""
import pathlib
import statistics
import subprocess
import sys
import time
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "experiments" / "dispatch")]
from los.models import OpenAICompat  # noqa: E402
from dispatch import load_commands, load_lines, output_schema, system_prompt  # noqa: E402

URL = "http://127.0.0.1:8080"


def healthy():
    try:
        return urllib.request.urlopen(URL + "/health", timeout=2).status == 200
    except OSError:
        return False


def measure(model, n):
    placement = [] if n == "auto" else ["-ngl", "all", "--n-cpu-moe", n]
    log = open(ROOT / "runtime" / f"sweep-{n}.log", "w")
    server = subprocess.Popen([ROOT / "scripts" / "serve.sh", model, *placement], stdout=log, stderr=log)
    try:
        start = time.time()
        while not healthy():
            if server.poll() is not None:
                return f"| {n} | did not start, see runtime/sweep-{n}.log | | | | |"
            time.sleep(1)
        load = time.time() - start
        vram = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                              capture_output=True, text=True).stdout.strip()
        commands = load_commands(200)
        system, schema = system_prompt(commands), output_schema(commands)
        student = OpenAICompat(URL + "/v1", model)
        metas = [student.complete(system, line["line"], schema)[1] for line in load_lines()[:12]]
        warm = metas[1:]
        return (f"| {n} | {load:.0f} | {vram} | {metas[0]['prompt_tokens_per_second']:.0f} | "
                f"{statistics.median(m['output_tokens_per_second'] for m in warm):.0f} | "
                f"{statistics.median(m['seconds'] for m in warm):.2f} |")
    finally:
        server.terminate()
        server.wait()


def main():
    model, counts = sys.argv[1], sys.argv[2:]
    print("| Expert layers in RAM | Load, s | VRAM, MiB | Prompt tokens/s | Output tokens/s | Seconds per line |")
    print("|---|---|---|---|---|---|")
    for n in counts:
        print(measure(model, n), flush=True)


if __name__ == "__main__":
    main()
