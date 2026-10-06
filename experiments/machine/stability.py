#!/usr/bin/env python3
"""Decode stability: run one program many times and count how often each step becomes the same
micro-op.

Two conditions. Live: every run reads the machine again, so what the registers hold differs a
little from run to run. Frozen: the first run is recorded and the others replay its command
outputs, so every run decodes exactly the same inputs.

Every cycle is saved in results/PROGRAM-CONDITION.jsonl, and a report is printed.

Usage: stability.py PROGRAM [--runs 20] [--url http://127.0.0.1:8080/v1] [--model NAME]
"""
import argparse
import collections
import json
import pathlib
import statistics
import sys

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from los import machine, plugins  # noqa: E402
from los.models import OpenAICompat  # noqa: E402


def runs_of(name, program, table, model, count, frozen):
    """The cycles of `count` runs, as one list per run."""
    runs, replay = [], None
    for _ in range(count):
        cycles = []
        machine.run(name, program, table, model, replay=replay, record=cycles.append)
        runs.append(cycles)
        if frozen and replay is None:
            replay = machine.recording(cycles)
    return runs


def report(condition, runs):
    print(f"\n### {condition}\n")
    print("| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |")
    print("|---|---|---|---|---|---|")
    notes = []
    for step in range(len(runs[0])):
        cycles = [run[step] for run in runs if len(run) > step]
        ops = collections.Counter(json.dumps(cycle["micro_op"], sort_keys=True) for cycle in cycles)
        inputs = {json.dumps(cycle["registers"], sort_keys=True) for cycle in cycles}
        print(f"| {step + 1} | {cycles[0]['instruction']} | {len(ops)} | {ops.most_common(1)[0][1]}/{len(cycles)} | "
              f"{len(inputs)} | {statistics.median(cycle['seconds'] for cycle in cycles):.2f} |")
        if cycles[0]["micro_op"]["op"] == "set":
            values = collections.Counter(cycle["micro_op"]["value"] for cycle in cycles)
            notes.append(f"Step {step + 1} stored " + "; ".join(f"\"{value}\" ({count})" for value, count in values.most_common(4)) +
                         (f"; and {len(values) - 4} more" if len(values) > 4 else ""))
    print()
    for note in notes:
        print("- " + note)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("program", help="a command written as a program, such as sys.health")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="gemma-4-26B-A4B-it-qat-q4_0.gguf")
    opts = parser.parse_args()

    table = plugins.load(ROOT / "plugins")
    program = table[opts.program].program
    model = OpenAICompat(opts.url, opts.model)
    print(f"## {opts.program}, {opts.runs} runs per condition, decoded by {opts.model}")
    for condition, frozen in (("Live", False), ("Frozen", True)):
        runs = runs_of(opts.program, program, table, model, opts.runs, frozen)
        path = HERE / "results" / f"{opts.program}-{condition.lower()}.jsonl"
        path.write_text("".join(json.dumps(cycle) + "\n" for run in runs for cycle in run))
        report(f"{condition}: " + ("every run replays the first run's command outputs" if frozen
                                   else "every run reads the machine again"), runs)


if __name__ == "__main__":
    main()
