#!/usr/bin/env python3
"""Search for a faster way to start the model server, and make serve.sh use it.

Tries the default settings, then alternatives for how weights are loaded, how many threads are
used and how layers are split between RAM and GPU. Each one is timed on recorded lines from the
dispatch experiment. A setting is kept only if every line gets the answer on record and enough
GPU memory stays free, and only if it is clearly faster than the best so far.

The winner is written to runtime/tuned/MODEL.args, which scripts/serve.sh reads.

Usage: tune-server.py MODEL.gguf [--record TAG] [--size 200] [--lines 100] [--headroom 500] [--dry-run]
"""
import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "experiments" / "dispatch")]
from dispatch import RESULTS, table_of  # noqa: E402  the experiment's module
from los import tune  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model")
    parser.add_argument("--record", default="shell-gemma4-26b", help="tag of the recorded answers to replay")
    parser.add_argument("--size", type=int, default=200, help="command table size the record was made with")
    parser.add_argument("--lines", type=int, default=100, help="how many recorded lines to replay")
    parser.add_argument("--headroom", type=int, default=500, help="MiB of GPU memory that must stay free")
    parser.add_argument("--dry-run", action="store_true", help="report only; do not change what serve.sh uses")
    opts = parser.parse_args()

    if tune.healthy(8080):
        sys.exit("A model server is already running on port 8080. Stop it first: tuning starts its own.")
    rows = [json.loads(row) for row in (RESULTS / f"{opts.record}-{opts.size}.jsonl").read_text().splitlines()]
    record = [(row["line"], None if row["command"] == "none" else row["command"],
               {arg["name"]: arg["value"] for arg in row["args"]}) for row in rows[:opts.lines]]
    table = table_of(opts.size)
    logs = ROOT / "runtime" / "tuned"
    logs.mkdir(parents=True, exist_ok=True)

    print(tune.HEADER, flush=True)
    tried = []

    def run(args):
        with open(logs / f"try-{len(tried)}.log", "w") as log:
            result = tune.measure(opts.model, args, table, record, opts.headroom, log=log)
        tried.append(result)
        print(tune.row(result), flush=True)
        return result

    best, results = tune.search(run, tune.stages())
    if best is None:
        sys.exit(f"\nThe default settings do not reproduce the record ({results[0].problem}), so there is "
                 "nothing to compare against. The record may have been made with another build or model.")

    default = results[0]
    if best.args:
        print(f"\nFastest accepted: {' '.join(best.args)}, {best.seconds:.2f} s per line against "
              f"{default.seconds:.2f} s by default ({100 * (1 - best.seconds / default.seconds):.0f}% faster).")
    else:
        print(f"\nNo accepted setting beat the defaults ({default.seconds:.2f} s per line) "
              f"by the {100 * tune.MARGIN:.0f}% margin.")
    (logs / f"{opts.model}.json").write_text(json.dumps([vars(result) for result in results], indent=1) + "\n")
    if opts.dry_run:
        print("Dry run: serve.sh is unchanged.")
    else:
        path = tune.apply(opts.model, best)
        print(f"serve.sh will use {path.relative_to(ROOT)}." if best.args else
              "The defaults won, so serve.sh uses no tuned settings for this model.")


if __name__ == "__main__":
    main()
