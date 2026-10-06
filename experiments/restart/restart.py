#!/usr/bin/env python3
"""Restart experiment: does the model server give the same answers after a restart?

One condition is one way of starting the server: with nothing fixed, so that llama.cpp splits the
weights between RAM and GPU itself, or with a fixed split; and with the GPU otherwise free, or
with another program holding some of its memory. The server is started several times under a
condition, and every start replays recorded cases twice.

Cached: the 100 dispatch lines at 200 commands and every input the steps of sys.health have been
decoded for, in the same order from a fresh start, with the server's prompt cache on. This is
how the shell has asked so far. The first start can also replay them in reverse order.

Fresh: 50 dispatch lines at 10 commands and the same decodes, with the prompt cache off, so the
server works every prompt out from its first token. Odd starts ask in order, even starts in
reverse.

Every answer is saved in results/CONDITION.jsonl and every start in results/CONDITION-starts.jsonl.

Usage: restart.py CONDITION [--starts 3] [--hold MIB] [--fixed] [--reversed] [--model NAME] [--port 8091]
       restart.py --report      # compare everything saved, no model needed
"""
import argparse
import collections
import json
import os
import pathlib
import statistics
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "experiments" / "dispatch")]
from dispatch import RESULTS as DISPATCHED, table_of  # noqa: E402  the dispatch experiment's module
from los import dispatch, machine, plugins, split, tune  # noqa: E402
from los.models import OpenAICompat  # noqa: E402

RESULTS = HERE / "results"
MACHINE = ROOT / "experiments" / "machine" / "results"
PROGRAM = "sys.health"
CACHED, FRESH = 200, 10         # the table size of the dispatch lines each kind of replay uses
FRESH_LINES = 50


def cases():
    """Every case as (name, table size or "decode", what to ask, the answer on record)."""
    found = []
    for size, count in ((CACHED, 100), (FRESH, FRESH_LINES)):
        for row in (DISPATCHED / f"shell-gemma4-26b-{size}.jsonl").read_text().splitlines()[:count]:
            row = json.loads(row)
            answer = [row["command"], {arg["name"]: arg["value"] for arg in row["args"]}]
            found.append((f"dispatch, {size} commands, line {row['id']}", size, row["line"], answer))
    program = plugins.load(ROOT / "plugins")[PROGRAM].program
    seen = {}
    for path in sorted(MACHINE.glob(f"{PROGRAM}-*.jsonl")):
        for cycle in map(json.loads, path.read_text().splitlines()):
            if cycle["how"] == "model":
                shown = machine.visible(cycle["instruction"], program, cycle["registers"])
                seen.setdefault((cycle["step"], json.dumps(shown, sort_keys=True)), cycle["micro_op"])
    for step, shown, micro_op in program.pins:      # a pinned input is never decoded in use; here it is
        seen[step, json.dumps(shown, sort_keys=True)] = micro_op
    for (step, shown), micro_op in sorted(seen.items()):
        found.append((f"decode, step {step}, {shown}", "decode", (step, json.loads(shown)), micro_op))
    return found


def replays(number, todo, also_reversed):
    """What one start is asked: (name of the replay, cases in order, extra request settings)."""
    cached = [case for case in todo if case[1] != FRESH]
    fresh = [case for case in todo if case[1] != CACHED]
    return ([("cached", cached, None)] +
            [("cached, reverse order", cached[::-1], None)] * (also_reversed and number == 1) +
            [("fresh", fresh if number % 2 else fresh[::-1], {"cache_prompt": False})])


def answer(model, kind, asked, plugged):
    if kind == "decode":
        step, registers = asked
        program = plugged[PROGRAM].program
        micro_op, meta = machine.decode(model, program, plugged, program.instructions[step - 1], registers)
        return micro_op, meta["seconds"]
    choice = dispatch.ask(model, table_of(kind), asked)
    return [choice.command or "none", choice.args], choice.meta["seconds"]


def start(opts, number, todo):
    """One start of the server. Returns what is known about the start and the answers it gave."""
    args = fixed_arguments() if opts.fixed else []
    about = {"condition": opts.condition, "start": number, "fixed": opts.fixed, "held": opts.hold,
             "gpu_free_before": tune.gpu_free()}
    holder = None
    if opts.hold:
        holder = subprocess.Popen([HERE / "hold-gpu.py", str(opts.hold)], stdout=subprocess.PIPE, text=True)
        holder.stdout.readline()                    # it prints one line once the memory is held
        about["gpu_free_held"] = tune.gpu_free()
    environment = {**os.environ, "LOS_NO_TUNED": "1", "LOS_NO_SPLIT": "1", "PORT": str(opts.port)}
    answers, log, loading = [], [], threading.Event()
    loading.set()
    server = subprocess.Popen([ROOT / "scripts" / "serve.sh", opts.model, "-lv", "5", *args], env=environment,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    # The debug log says where every tensor went. Only the part up to the end of loading is kept.
    reader = threading.Thread(target=lambda: [log.append(line) for line in server.stdout if loading.is_set()])
    reader.start()
    try:
        while not tune.healthy(opts.port) and server.poll() is None:
            time.sleep(1)
        about["started"] = server.poll() is None
        loading.clear()
        if about["started"]:
            about["gpu_free_loaded"] = tune.gpu_free()
            plugged = plugins.load(ROOT / "plugins")
            for name, order, extra in replays(number, todo, opts.reversed):
                model = OpenAICompat(f"http://127.0.0.1:{opts.port}/v1", opts.model, extra)
                for case, kind, asked, _ in order:
                    given, seconds = answer(model, kind, asked, plugged)
                    answers.append({"condition": opts.condition, "start": number, "replay": name, "case": case,
                                    "answer": given, "seconds": seconds})
    finally:
        server.terminate()
        server.wait()
        reader.join()
        if holder:
            holder.terminate()
            holder.wait()
    if about["started"]:
        layers, kept, on_gpu = split.read("".join(log))
        about.update(gpu_layers=layers, kept_in_ram=kept, weights_on_gpu=on_gpu)
    else:
        about["error"] = next((line.split(" E ", 1)[1].strip() for line in log if " E " in line), "")
    return about, answers


def fixed_arguments():
    """The split to fix: the one the first start with nothing fixed and a free GPU chose."""
    first = json.loads((RESULTS / "auto-starts.jsonl").read_text().splitlines()[0])
    return split.arguments(first["gpu_layers"], first["kept_in_ram"])


def saved(suffix):
    """Saved rows by condition: the starts for "-starts", the answers for ""."""
    rows = collections.defaultdict(list)
    for path in sorted(RESULTS.glob("*.jsonl")):
        if path.stem.endswith("-starts") == bool(suffix):
            for row in map(json.loads, path.read_text().splitlines()):
                rows[row["condition"]].append(row)
    return rows


def report():
    starts, answers = saved("-starts"), saved("")
    given = collections.defaultdict(dict)           # (condition, start, replay) -> case -> answer
    for rows in answers.values():
        for row in rows:
            given[row["condition"], row["start"], row["replay"]][row["case"]] = row["answer"]
    first = starts["auto"][0]
    order = sorted(starts, key=lambda name: (starts[name][0]["fixed"], starts[name][0]["held"], name))

    def differing(one, other):
        return [case for case in one if case in other and one[case] != other[case]]

    def against_first(name, number, replay):
        mine, reference = given[name, number, replay], given["auto", 1, replay]
        return f"{len(differing(reference, mine))} of {len(reference)}" if mine else "not asked"

    print("### Each start\n")
    print("Answers are compared with those of the first start with nothing fixed.\n")
    print("| Condition | Start | GPU MiB free before the server | Weights on the GPU, MiB | Same split as the first | "
          "Cached answers that differ | Fresh answers that differ |")
    print("|---|---|---|---|---|---|---|")
    for name in order:
        for about in starts[name]:
            free = about.get("gpu_free_held", about["gpu_free_before"]) or "not recorded"
            if not about["started"]:
                print(f"| {name} | {about['start']} | {free} | did not start | | | |")
                continue
            same = (about["gpu_layers"], about["kept_in_ram"]) == (first["gpu_layers"], first["kept_in_ram"])
            print(f"| {name} | {about['start']} | {free} | {about['weights_on_gpu']} | {'yes' if same else 'no'} | "
                  f"{against_first(name, about['start'], 'cached')} | {against_first(name, about['start'], 'fresh')} |")

    print("\n### The same start, asked in another order\n")
    print("| Condition | Start | Cached answers that differ when asked in reverse |")
    print("|---|---|---|")
    for (name, number, replay), mine in sorted(given.items()):
        if replay == "cached, reverse order":
            print(f"| {name} | {number} | {len(differing(given[name, number, 'cached'], mine))} of {len(mine)} |")

    print("\n### What a fresh answer costs\n")
    print("| Case | Median seconds, cached | Median seconds, fresh |")
    print("|---|---|---|")
    seconds = collections.defaultdict(list)
    for rows in answers.values():
        for row in rows:
            kind = "decode" if row["case"].startswith("decode") else row["case"].rsplit(",", 1)[0]
            if "seconds" in row:
                seconds[kind, row["replay"].split(",")[0]].append(row["seconds"])
    recorded = [json.loads(row)["meta"]["seconds"] for row in
                (DISPATCHED / f"shell-gemma4-26b-{FRESH}.jsonl").read_text().splitlines()[:FRESH_LINES]]
    seconds[f"dispatch, {FRESH} commands", "cached"] = recorded     # timed by the dispatch experiment, cache on
    for kind in sorted({kind for kind, _ in seconds}):
        cells = [f"{statistics.median(seconds[kind, replay]):.2f}" if seconds[kind, replay] else "not measured"
                 for replay in ("cached", "fresh")]
        print(f"| {kind} | {' | '.join(cells)} |")

    record = {name: expected for name, _, _, expected in cases()}
    for replay in ("cached", "fresh"):
        mine = given["auto", 1, replay]
        print(f"\nAgainst the answers recorded by the earlier experiments, the first start's {replay} answers "
              f"differ on {len(differing(mine, record))} of {len(mine)}.")

    changed = collections.defaultdict(lambda: collections.defaultdict(list))
    for (name, number, replay), mine in sorted(given.items()):
        reference = given["auto", 1, replay.split(",")[0]]
        for case in differing(reference, mine):
            changed[case, replay.split(",")[0]][json.dumps(mine[case], ensure_ascii=False)].append(
                f"{name} {number}" + (", reversed" if "reverse" in replay else ""))
    print("\n### Every case that got another answer somewhere\n")
    asked = list(record)
    for (case, replay), others in sorted(changed.items(), key=lambda item: asked.index(item[0][0])):
        first_answer = json.dumps(given["auto", 1, replay][case], ensure_ascii=False)
        print(f"- {case} ({replay})\n  - first start: `{first_answer}`")
        for other, where in others.items():
            print(f"  - `{other}`: {'; '.join(where)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("condition", nargs="?", help="a name for this way of starting, such as auto or fixed")
    parser.add_argument("--starts", type=int, default=3)
    parser.add_argument("--hold", type=int, default=0, help="MiB of GPU memory another program holds meanwhile")
    parser.add_argument("--fixed", action="store_true", help="start with the split the first auto start chose")
    parser.add_argument("--reversed", action="store_true", help="the first start also replays in reverse order")
    parser.add_argument("--model", default="gemma-4-26B-A4B-it-qat-q4_0.gguf")
    parser.add_argument("--port", type=int, default=8091)
    parser.add_argument("--report", action="store_true", help="compare everything saved, without running anything")
    opts = parser.parse_args()
    if opts.report:
        return report()
    if not opts.condition:
        parser.error("name the condition")
    if tune.healthy(opts.port):
        sys.exit(f"Something is already serving on port {opts.port}.")

    todo = cases()
    RESULTS.mkdir(exist_ok=True)
    for number in range(1, opts.starts + 1):
        about, answers = start(opts, number, todo)
        with (RESULTS / f"{opts.condition}-starts.jsonl").open("a") as out:
            out.write(json.dumps(about) + "\n")
        if answers:
            with (RESULTS / f"{opts.condition}.jsonl").open("a") as out:
                out.writelines(json.dumps(row, ensure_ascii=False) + "\n" for row in answers)
        print(f"{opts.condition}, start {number}: " +
              (f"{about['weights_on_gpu']} MiB of weights on the GPU, {len(answers)} answers" if about["started"]
               else f"did not start ({about['error']})"), flush=True)


if __name__ == "__main__":
    main()
