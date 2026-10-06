#!/usr/bin/env python3
"""Decode stability: run one program many times and count how often each step becomes the same
micro-op.

Three conditions. Live: every run reads the machine again, so what the registers hold differs a
little from run to run. Frozen: the first run is recorded and the others replay its command
outputs, so every run decodes exactly the same inputs. In both, every instruction goes to the
model. With memory: live runs again, starting from an empty memory, to see how many decodes it
answers and what that saves.

Every cycle is saved in results/PROGRAM-CONDITION.jsonl, and a report is printed.

Usage: stability.py PROGRAM [--runs 20] [--url http://127.0.0.1:8080/v1] [--model NAME]
       stability.py PROGRAM --report      # print the report again from the saved cycles, no model needed
"""
import argparse
import collections
import json
import os
import pathlib
import statistics
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
from los import machine, plugins  # noqa: E402
from los.models import OpenAICompat  # noqa: E402


def runs_of(name, program, table, model, count, frozen=False, remember=False):
    """The cycles of `count` runs, as one list per run."""
    runs, replay = [], None
    for _ in range(count):
        cycles = []
        machine.run(name, program, table, model, replay=replay, record=cycles.append, remember=remember)
        runs.append(cycles)
        if frozen and replay is None:
            replay = machine.recording(cycles)
    return runs


def decoder_seconds(runs):
    """Median time one run spends in the decoder."""
    return statistics.median(sum(cycle["seconds"] or 0 for cycle in run) for run in runs)


def report_memory(runs, without):
    print("\n### With memory: every run reads the machine again, memory starts empty\n")
    print("| Step | Instruction | Runs answered from memory |")
    print("|---|---|---|")
    for step in range(len(runs[0])):
        cycles = [run[step] for run in runs if len(run) > step]
        print(f"| {step + 1} | {cycles[0]['instruction']} | "
              f"{sum(cycle['how'] == 'memory' for cycle in cycles)}/{len(cycles)} |")
    recalled = sum(cycle["how"] == "memory" for run in runs for cycle in run)
    total = sum(len(run) for run in runs)
    mean = statistics.mean(sum(cycle["seconds"] or 0 for cycle in run) for run in runs)
    print(f"\nMemory answered {recalled} of {total} cycles. Time a run spends in the decoder: "
          f"{decoder_seconds(without):.1f} s without memory (median); with it, {mean:.1f} s on average "
          f"and {decoder_seconds(runs):.1f} s at the median.")


def report(condition, runs, program):
    print(f"\n### {condition}\n")
    print("| Step | Instruction | Different micro-ops | Runs giving the commonest | Different inputs | Median seconds |")
    print("|---|---|---|---|---|---|")
    notes = []
    for step in range(len(runs[0])):
        cycles = [run[step] for run in runs if len(run) > step]
        ops = collections.Counter(json.dumps(cycle["micro_op"], sort_keys=True) for cycle in cycles)
        inputs = {json.dumps(machine.visible(cycle["instruction"], program, cycle["registers"]), sort_keys=True)
                  for cycle in cycles}
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
    parser.add_argument("--report", action="store_true", help="report from the saved cycles without running anything")
    opts = parser.parse_args()

    table = plugins.load(ROOT / "plugins")
    program = table[opts.program].program
    model = OpenAICompat(opts.url, opts.model)
    print(f"## {opts.program}, {opts.runs} runs per condition, decoded by {opts.model}")
    def saved(condition):
        return HERE / "results" / f"{opts.program}-{condition}.jsonl"

    def save(condition, runs):
        saved(condition).write_text("".join(json.dumps(cycle) + "\n" for run in runs for cycle in run))

    def load(condition):
        runs = collections.defaultdict(list)
        for row in saved(condition).read_text().splitlines():
            runs[json.loads(row)["run"]].append(json.loads(row))
        return list(runs.values())

    if opts.report:
        live, frozen, remembered = load("live"), load("frozen"), load("memory")
    else:
        live = runs_of(opts.program, program, table, model, opts.runs)
        save("live", live)
        frozen = runs_of(opts.program, program, table, model, opts.runs, frozen=True)
        save("frozen", frozen)
        with tempfile.TemporaryDirectory() as folder:      # an empty memory that is thrown away afterwards
            os.environ["LOS_STATE"] = folder
            remembered = runs_of(opts.program, program, table, model, opts.runs, remember=True)
        save("memory", remembered)
    report("Live: every run reads the machine again", live, program)
    report("Frozen: every run replays the first run's command outputs", frozen, program)
    report_memory(remembered, live)


if __name__ == "__main__":
    main()
