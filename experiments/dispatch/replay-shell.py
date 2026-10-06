#!/usr/bin/env python3
"""Run the shell's own dispatcher over the experiment's lines and tables.

Answers are recorded in the experiment's format, so score.py can compare them with the teacher's.
Rerun this whenever los/dispatch.py changes its prompt or schema, to see that nothing got worse.

Usage: replay-shell.py TAG [--sizes 10 50 200] [--url URL] [--model NAME]
"""
import argparse
import datetime
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from dispatch import RESULTS, load_lines, table_of  # noqa: E402  the experiment's module
from los import dispatch as shell_dispatch  # noqa: E402
from los.models import OpenAICompat  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("tag")
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200])
    parser.add_argument("--url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--model", default="gemma-4-26B-A4B-it-qat-q4_0.gguf")
    opts = parser.parse_args()

    model = OpenAICompat(opts.url, opts.model)
    for size in opts.sizes:
        table, path = table_of(size), RESULTS / f"{opts.tag}-{size}.jsonl"
        done = {json.loads(row)["id"] for row in path.read_text().splitlines()} if path.exists() else set()
        todo = [line for line in load_lines() if line["id"] not in done]
        for line in todo:
            choice = shell_dispatch.ask(model, table, line["line"])
            record = {"id": line["id"], "line": line["line"], "command": choice.command or "none",
                      "args": [{"name": name, "value": value} for name, value in choice.args.items()],
                      "provider": model.provider, "model": model.model,
                      "date": datetime.date.today().isoformat(), "meta": choice.meta}
            with path.open("a") as out:
                out.write(json.dumps(record) + "\n")
        print(f"{opts.tag}-{size}: {len(done)} already recorded, {len(todo)} asked")


if __name__ == "__main__":
    main()
