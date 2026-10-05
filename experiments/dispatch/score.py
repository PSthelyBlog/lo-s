#!/usr/bin/env python3
"""Compare a student's recorded dispatches with the teacher's, per table size.

Usage: score.py TEACHER_TAG STUDENT_TAG [SECOND_RUN_TAG] [--sizes 10 50 200] [--skip-examples]
Prints a markdown report. With SECOND_RUN_TAG, also reports how often two runs of the
student gave the same answer. --skip-examples leaves out the lines that dispatch.py --examples
shows to the model, so runs with and without examples are scored on the same lines.
"""
import argparse
import json
import pathlib
import statistics

from dispatch import is_example, load_lines

RESULTS = pathlib.Path(__file__).parent / "results"


def load(tag, size):
    path = RESULTS / f"{tag}-{size}.jsonl"
    return {row["id"]: row for row in map(json.loads, path.read_text().splitlines())}


def args_of(record):
    return {arg["name"].strip().lower(): arg["value"].strip().lower() for arg in record["args"]}


def pct(part, whole):
    return f"{part}/{whole} ({100 * part / whole:.0f}%)" if whole else "0/0"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("teacher")
    parser.add_argument("student")
    parser.add_argument("second_run", nargs="?")
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 50, 200])
    parser.add_argument("--skip-examples", action="store_true")
    opts = parser.parse_args()

    rows, disagreements = [], []
    for size in opts.sizes:
        teacher, student = load(opts.teacher, size), load(opts.student, size)
        ids = sorted(teacher.keys() & student.keys(), key=int)
        if opts.skip_examples:
            ids = [i for i in ids if not is_example(i)]
        real = [i for i in ids if teacher[i]["command"] != "none"]
        none = [i for i in ids if teacher[i]["command"] == "none"]
        same = [i for i in ids if teacher[i]["command"] == student[i]["command"]]
        same_real = [i for i in real if i in same]
        wrong_command = [i for i in real if student[i]["command"] not in ("none", teacher[i]["command"])]
        missed = [i for i in real if student[i]["command"] == "none"]
        invented = [i for i in none if student[i]["command"] != "none"]
        same_names = [i for i in same_real if args_of(teacher[i]).keys() == args_of(student[i]).keys()]
        same_args = [i for i in same_real if args_of(teacher[i]) == args_of(student[i])]
        seconds = [student[i]["meta"]["seconds"] for i in ids]
        speeds = [s for i in ids if (s := student[i]["meta"].get("output_tokens_per_second"))]
        row = {
            "Commands in table": size,
            "Lines compared": len(ids),
            "Same command as teacher": pct(len(same), len(ids)),
            "Teacher picked a command: student picked another": pct(len(wrong_command), len(real)),
            "Teacher picked a command: student said none": pct(len(missed), len(real)),
            "Teacher said none: student ran a command": pct(len(invented), len(none)),
            "Same command: same parameter names": pct(len(same_names), len(same_real)),
            "Same command: same names and values": pct(len(same_args), len(same_real)),
            "Median seconds per line": f"{statistics.median(seconds):.2f}",
            "Median output tokens per second": f"{statistics.median(speeds):.0f}" if speeds else "n/a",
        }
        if opts.second_run:
            second = load(opts.second_run, size)
            both = [i for i in ids if i in second]
            stable = [i for i in both if (student[i]["command"], args_of(student[i])) ==
                      (second[i]["command"], args_of(second[i]))]
            row["Two runs gave the same answer"] = pct(len(stable), len(both))
        rows.append(row)
        disagreements += [(size, i, teacher[i]["line"], teacher[i]["command"], student[i]["command"])
                          for i in ids if i not in same]

    print(f"## {opts.student} against {opts.teacher}\n")
    print("| | " + " | ".join(str(r["Commands in table"]) for r in rows) + " |")
    print("|---|" + "---|" * len(rows))
    for key in list(rows[0])[1:]:
        print(f"| {key} | " + " | ".join(str(r[key]) for r in rows) + " |")

    print("\n### Lines where the student's command differs\n")
    print("| Table | Line | Typed | Teacher | Student |\n|---|---|---|---|---|")
    for size, i, line, want, got in disagreements:
        print(f"| {size} | {i} | {line} | {want} | {got} |")

    largest = max(opts.sizes)
    teacher, intents = load(opts.teacher, largest), {l["id"]: l["intent"] for l in load_lines()}
    off = [(i, teacher[i]["line"], intents[i], teacher[i]["command"])
           for i in sorted(teacher, key=int) if teacher[i]["command"] != intents[i]]
    print(f"\n### Teacher against the author's intended command, table of {largest}\n")
    print(f"Same on {pct(len(teacher) - len(off), len(teacher))} lines.\n")
    if off:
        print("| Line | Typed | Author intended | Teacher |\n|---|---|---|---|")
        for i, line, intent, got in off:
            print(f"| {i} | {line} | {intent} | {got} |")


if __name__ == "__main__":
    main()
