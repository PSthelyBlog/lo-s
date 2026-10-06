#!/usr/bin/env python3
"""Dispatch experiment: one model maps every line in lines.tsv to a command, at each table size.

Labels are recorded once in results/TAG-SIZE.jsonl and never asked for again; rerunning only
fills the gaps. Teacher and student get the same system prompt, the same user message and the
same schema.

Usage:
  dispatch.py claude-cli TAG [--model claude-opus-5-5] [--workers 4]
  dispatch.py openai TAG --url http://127.0.0.1:8080/v1 --model NAME [--extra JSON]
  dispatch.py openai TAG ... --examples TEACHER_TAG     # every fifth line becomes a worked example
"""
import argparse
import datetime
import json
import pathlib
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))
from los.models import ClaudeCli, OpenAICompat, complete_valid  # noqa: E402
from los.plugins import Command  # noqa: E402
RESULTS = HERE / "results"

INSTRUCTIONS = """\
You are the dispatcher of a command-line operating system. Users normally type structured \
commands. The line you receive did not parse as one, so it is probably plain language. Decide \
which command from the table, if any, the user meant.

Pick a command only when running it would do what the user asked for. A wrong command is worse \
than no command: when nothing in the table does the job, answer "none", and the request is \
queued so that a new command can be written for it. When several commands could serve, pick \
the most specific one.

For args, use only the parameter names listed for the chosen command, take the values from the \
user's line, and leave out parameters the line says nothing about. With "none", args is empty.

Reply with JSON of the form {"command": "<name or none>", "args": [{"name": "<parameter>", \
"value": "<value>"}]}.

Commands, one per line as: name | what it does | parameters
"""


def load_commands(size):
    """The first `size` rows of the table, sorted by name so position carries no hint."""
    rows = [line.split("\t") for line in (HERE / "commands.tsv").read_text().splitlines()]
    return sorted(rows[:size])


def table_of(size):
    """The first `size` commands as the shell's command table."""
    table = {}
    for name, description, *params in load_commands(size):
        names = [param.strip() for param in params[0].split(",")] if params else []
        table[name] = Command(name, description, dict.fromkeys(names, ""))
    return table


def load_lines():
    return [dict(zip(("id", "line", "intent"), row.split("\t")))
            for row in (HERE / "lines.tsv").read_text().splitlines()]


def system_prompt(commands):
    return INSTRUCTIONS + "\n".join(" | ".join(row) for row in commands)


def output_schema(commands):
    arg = {"type": "object", "additionalProperties": False, "required": ["name", "value"],
           "properties": {"name": {"type": "string"}, "value": {"type": "string"}}}
    return {"type": "object", "additionalProperties": False, "required": ["command", "args"],
            "properties": {"command": {"type": "string", "enum": [row[0] for row in commands] + ["none"]},
                           "args": {"type": "array", "items": arg}}}


def is_example(line_id):
    """Every fifth line is set aside as a worked example when --examples is used."""
    return int(line_id) % 5 == 0


def worked_examples(teacher_tag, size):
    """The set-aside lines with the teacher's recorded answers, as text for the system prompt."""
    rows = [json.loads(row) for row in (RESULTS / f"{teacher_tag}-{size}.jsonl").read_text().splitlines()]
    shown = sorted((row for row in rows if is_example(row["id"])), key=lambda row: int(row["id"]))
    return "\n\nExamples, one per line as: typed line => answer\n" + "\n".join(
        f"{row['line']} => {json.dumps({'command': row['command'], 'args': row['args']})}" for row in shown)


def run(model, tag, size, workers, examples=None):
    commands = load_commands(size)
    system, schema = system_prompt(commands), output_schema(commands)
    if examples:
        system += worked_examples(examples, size)
    path = RESULTS / f"{tag}-{size}.jsonl"
    done = {json.loads(row)["id"] for row in path.read_text().splitlines()} if path.exists() else set()
    todo = [line for line in load_lines()
            if line["id"] not in done and not (examples and is_example(line["id"]))]
    lock = threading.Lock()

    def label(line):
        try:
            output, meta = complete_valid(model, system, line["line"], schema)
        except RuntimeError as error:
            print(f"  {tag}-{size} line {line['id']}: {error}")
            return
        record = {"id": line["id"], "line": line["line"], **output, "provider": model.provider,
                  "model": model.model, "date": datetime.date.today().isoformat(), "meta": meta}
        with lock, path.open("a") as out:
            out.write(json.dumps(record) + "\n")

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(label, todo))
    print(f"{tag}-{size}: {len(done)} already recorded, {len(todo)} asked")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("provider", choices=["claude-cli", "openai"])
    parser.add_argument("tag", help="name for this model's result files")
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200])
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--url", default="http://127.0.0.1:8080/v1")
    parser.add_argument("--extra", default="{}", help="JSON merged into each openai request body")
    parser.add_argument("--examples", metavar="TEACHER_TAG",
                        help="show every fifth line with this teacher's answer, and label only the rest")
    args = parser.parse_args()

    RESULTS.mkdir(exist_ok=True)
    if args.provider == "claude-cli":
        model = ClaudeCli(args.model)
    else:
        model = OpenAICompat(args.url, args.model, json.loads(args.extra))
    for size in args.sizes:
        run(model, args.tag, size, args.workers, args.examples)


if __name__ == "__main__":
    main()
