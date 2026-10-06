"""Plugins: which commands exist, what they take and how to run them.

A plugin is a directory holding `plugin.toml`, which declares its commands, and `commands.py`,
which defines one function `do_<verb>` per command. Parameters arrive as keyword strings and the
function returns the text to show. Several directories may add commands under one plugin name;
a command written by a model lives in a directory of its own, so removing it is one deletion.

A command can instead be a program: a list of plain-language instructions that the machine runs
(see machine.py). It is declared entirely in `plugin.toml` and needs no function.
"""
import dataclasses
import hashlib
import importlib.util
import json
import pathlib
import tomllib

EFFECTS = ("read", "write", "destructive")


class CommandError(Exception):
    """A command could not do what was asked. The message is shown to the user as is."""


@dataclasses.dataclass(frozen=True)
class Program:
    instructions: tuple         # plain-language instructions, run in order
    registers: tuple            # the registers the program may use
    calls: tuple                # the commands it may call
    result: str                 # the register whose value is shown when it ends
    pins: tuple = ()            # (step, what the decoder would be shown, micro-op): decodes fixed by the author


@dataclasses.dataclass(frozen=True)
class Command:
    name: str                   # plugin.verb
    description: str
    params: dict                # parameter name -> hint for whoever fills it in, possibly empty
    effect: str = "read"        # read, write or destructive
    run: object = None          # the do_<verb> function, for a command written as code
    program: Program = None     # the instructions, for a command written as a program


def load(directory):
    """Read every plugin under `directory` into a table of commands keyed by name."""
    table = {}
    for manifest in sorted(pathlib.Path(directory).glob("*/plugin.toml")):
        spec = tomllib.loads(manifest.read_text())
        module, source = None, manifest.parent / "commands.py"
        if source.exists():
            module_spec = importlib.util.spec_from_file_location(
                "los_plugin_" + manifest.parent.name.replace(".", "_"), source)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
        for verb, entry in spec["commands"].items():
            name, effect = f"{spec['name']}.{verb}", entry.get("effect", "read")
            if effect not in EFFECTS:
                raise ValueError(f"{name}: effect must be one of {', '.join(EFFECTS)}, not {effect!r}")
            if name in table:
                raise ValueError(f"{name} is defined twice, the second time in {manifest.parent}")
            params = dict(entry.get("params", {}))
            if "program" in entry:
                pins = tuple((pin["step"], dict(pin["registers"]), dict(pin["micro_op"]))
                             for pin in entry.get("pins", []))
                program = Program(tuple(entry["program"]), tuple(entry["registers"]),
                                  tuple(entry.get("calls", [])), entry["result"], pins)
                table[name] = Command(name, entry["description"], params, effect, program=program)
            else:
                table[name] = Command(name, entry["description"], params, effect, getattr(module, f"do_{verb}"))
    for command in table.values():
        if command.program:
            _check_program(command, table)
    return table


def _check_program(command, table):
    """A program may only call plain commands that exist, none of them less careful than itself."""
    program = command.program
    if program.result not in program.registers or not set(command.params) <= set(program.registers):
        raise ValueError(f"{command.name}: its result and its parameters must be among its registers")
    for step, shown, micro_op in program.pins:
        if not 1 <= step <= len(program.instructions) or not set(shown) <= set(program.registers) \
                or micro_op.get("op") != "set" or micro_op.get("register") not in program.registers:
            raise ValueError(f"{command.name}: a pin must name a step, registers of the program, and a set micro-op")
    for name in program.calls:
        if name not in table or table[name].program:
            raise ValueError(f"{command.name} may only call commands written as code, and {name} is not one")
        if EFFECTS.index(table[name].effect) > EFFECTS.index(command.effect):
            raise ValueError(f"{command.name} is marked {command.effect} but may call {name}, "
                             f"which is {table[name].effect}")


def version(table):
    """A short fingerprint of the table. It is recorded with every label, because the right
    command for a line changes when commands are added."""
    canon = json.dumps(sorted([c.name, c.description, sorted(c.params)] for c in table.values()))
    return hashlib.sha256(canon.encode()).hexdigest()[:12]
