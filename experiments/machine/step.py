#!/usr/bin/env python3
"""Decode one step of a program for every combination of the register values you give.

A step that reads registers with few possible values can be checked completely this way, which
is worth doing whenever its instruction is reworded.

The decoder is the one los.toml gives the decode role, asked with the same settings as the shell
asks it, so what is checked here is what a run gets.

Usage: step.py PROGRAM STEP [REGISTER=VALUE,VALUE ...]
Example: step.py sys.health 5 mem_level=fine,worrying temp_level=fine,worrying
"""
import argparse
import itertools
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))
from los import machine, plugins  # noqa: E402
from los.models import provider  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("program")
    parser.add_argument("step", type=int)
    parser.add_argument("registers", nargs="*", metavar="REGISTER=VALUE,VALUE")
    opts = parser.parse_args()

    table = plugins.load(ROOT / "plugins")
    program = table[opts.program].program
    instruction = program.instructions[opts.step - 1]
    names = [item.split("=", 1)[0] for item in opts.registers]
    choices = [item.split("=", 1)[1].split(",") for item in opts.registers]
    config = tomllib.loads((ROOT / "los.toml").read_text())
    model = provider(config["providers"][config["roles"]["decode"]])
    print(f"{opts.program}, step {opts.step}: {instruction}\n")
    print("| " + " | ".join(names) + " | Micro-op |")
    print("|" + "---|" * (len(names) + 1))
    for values in itertools.product(*choices):
        op, _ = machine.decode(model, program, table, instruction, dict(zip(names, values)))
        if op["op"] == "set":
            did = f"set {op['register']} = {op['value']}"
        elif op["op"] == "call":
            did = f"call {op['command']} {op['args']} into {op['into']}"
        else:
            did = "halt"
        print("| " + " | ".join(values) + f" | {did} |")


if __name__ == "__main__":
    main()
