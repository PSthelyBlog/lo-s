"""The machine: runs a program one instruction at a time.

A program is a list of plain-language instructions. For each one the decode model returns a
single micro-op, and the machine applies it:

  call  runs a command and stores its output in a register
  set   stores a value the model worked out in a register
  halt  stops the program

The machine owns the program counter. It moves to the next instruction after every micro-op,
whatever the model says, so one instruction costs exactly one model call. The model never runs
anything and never copies a command's output: `call` puts it in the register directly.

The decoder is shown one instruction and only the registers that instruction names in backticks.
An instruction can also name a register in braces, as in {temp}. The decoder is not shown that
register; it writes {temp} in its micro-op and the machine fills in what the register holds. So
a value can be passed along without the model reading or retyping it.

Decoding is repeatable, so a decode is remembered: when everything the decoder would be shown has
been seen before, the recorded micro-op is used and the model is not asked.

Every cycle is recorded. A recorded run can be replayed, in which case commands are not run
again and their recorded outputs are used instead.
"""
import datetime
import hashlib
import json
import re
import time

from . import state
from .dispatch import table_text
from .models import complete_valid
from .plugins import CommandError, Program  # noqa: F401  Program is re-exported for callers

SHOWN = 2000    # characters of a register's value the decoder is shown
REFERENCE = re.compile(r"\{(\w+)\}")

INSTRUCTIONS = """\
You are the decoder of a small machine. The machine runs a program one instruction at a time. \
For each instruction you receive, reply with the single micro-op that carries it out. You do not \
run anything yourself and you do not see the rest of the program: the machine applies your \
micro-op and then moves on to the next instruction.

There are three micro-ops.
- call runs one command from the table below and stores its output in a register. Use it when \
the instruction asks to read, look up or do something that a command provides.
- set stores a value that you work out yourself in a register. Use it when the instruction asks \
you to judge, decide, summarise or write something from what the registers already hold.
- halt stops the program. Use it only when the instruction says to stop.

Registers hold text. An instruction names registers in backticks. Store into the register the \
instruction names, and when it refers to what a register holds, read that from the registers \
you are given. You are given only the registers the instruction names in backticks. When an \
instruction offers a fixed set of values, store exactly one of them.

An instruction may also write a register in braces, such as {name}. You are not shown what that \
register holds and you do not need to be. To use it, write {name} exactly like that in the value \
or argument, and the machine puts the register's contents in its place.

Reply with JSON in one of these forms:
{"micro_op": {"op": "call", "command": "<name>", "args": {"<parameter>": "<value>"}, "into": "<register>"}}
{"micro_op": {"op": "set", "register": "<register>", "value": "<text>"}}
{"micro_op": {"op": "halt"}}
"""


class Trap(CommandError):
    """The program could not continue."""


def system_prompt(program, table):
    allowed = {name: table[name] for name in program.calls}
    commands = table_text(allowed) if allowed else "(this program may call no commands)"
    return (INSTRUCTIONS + "\nCommands, one per line as: name | what it does | parameters\n" + commands +
            "\n\nRegisters: " + ", ".join(program.registers))


def schema(program, table):
    """One form per micro-op, and for call one per command the program may use, so the model can
    only name registers the program declares and parameters the command has."""
    def form(properties):
        return {"type": "object", "additionalProperties": False, "required": list(properties),
                "properties": properties}

    register = {"type": "string", "enum": list(program.registers)}
    forms = [form({"op": {"const": "call"}, "command": {"const": name},
                   "args": {"type": "object", "additionalProperties": False,
                            "properties": {param: {"type": "string"} for param in table[name].params}},
                   "into": register}) for name in program.calls]
    forms += [form({"op": {"const": "set"}, "register": register, "value": {"type": "string"}}),
              form({"op": {"const": "halt"}})]
    return form({"micro_op": {"anyOf": forms}})


def fill(text, program, registers, step):
    """The text with each {name} replaced by what that register holds."""
    def held(match):
        name = match.group(1)
        if name not in program.registers:
            return match.group(0)       # not a register: ordinary braces
        if name not in registers:
            raise Trap(f"step {step} refers to {{{name}}}, which holds nothing yet")
        return registers[name]

    return REFERENCE.sub(held, text)


def visible(instruction, program, registers):
    """What the decoder is shown of the registers: those the instruction names in backticks that
    hold something. A register named in braces is passed along unseen."""
    named = set(re.findall(r"`([^`]+)`", instruction))
    return {name: registers[name][:SHOWN] for name in program.registers if name in named and name in registers}


def decode(model, program, table, instruction, registers, known=None):
    """The micro-op that carries out one instruction. Returns (micro-op, meta).

    `known` maps a fingerprint of everything the decoder would be shown to an earlier decode.
    A match is returned without asking the model. A new decode is added to it and to the state
    folder. Pass None to always ask the model and remember nothing.
    """
    system, form = system_prompt(program, table), schema(program, table)
    user = (f"Instruction: {instruction}\n"
            f"Registers: {json.dumps(visible(instruction, program, registers), ensure_ascii=False)}")
    shown = json.dumps([model.provider, model.model, system, user, form], sort_keys=True)
    key = hashlib.sha256(shown.encode()).hexdigest()[:24]
    if known is not None and key in known:
        return known[key]["micro_op"], {"how": "memory", "seconds": 0, "saved": known[key]["seconds"]}
    output, meta = complete_valid(model, system, user, form)
    meta["how"] = "model"
    if known is not None:
        known[key] = {"key": key, "micro_op": output["micro_op"], "seconds": meta.get("seconds")}
        state.append("decodes", known[key])
    return output["micro_op"], meta


def run(name, program, table, model, args=None, replay=None, record=None, remember=True):
    """Run a program and return the value of its result register.

    `args` are loaded into the registers of the same names before the first instruction.
    `replay` maps a step to the (command, args, output) recorded for it; commands are then not
    run, and a call that differs from the recording is a trap. `record` receives each cycle;
    by default cycles go to the state folder. With `remember` off, every instruction goes to
    the model and no decode is kept.
    """
    record = record or (lambda cycle: state.append("cycles", cycle))
    known = {entry["key"]: entry for entry in state.read("decodes")} if remember else None
    registers = dict(args or {})
    run_id = f"{time.time_ns():x}"
    for step, instruction in enumerate(program.instructions, 1):
        before = dict(registers)
        try:
            micro_op, meta = decode(model, program, table, instruction, registers, known)
        except RuntimeError as error:
            raise Trap(f"step {step}: the decoder gave no usable micro-op ({error})")
        cycle = {"run": run_id, "date": datetime.date.today().isoformat(), "program": name, "step": step,
                 "instruction": instruction, "registers": before, "micro_op": micro_op, "how": meta["how"],
                 "seconds": meta.get("seconds"), "saved": meta.get("saved"),
                 "provider": model.provider, "model": model.model}
        if micro_op["op"] == "set":
            registers[micro_op["register"]] = fill(micro_op["value"], program, registers, step)
        elif micro_op["op"] == "call":
            filled = {param: fill(value, program, registers, step) for param, value in micro_op["args"].items()}
            cycle["output"] = registers[micro_op["into"]] = _call(micro_op, filled, table, step, replay)
        record(cycle)
        if micro_op["op"] == "halt":
            break
    if program.result not in registers:
        raise Trap(f"the program ended without storing anything in `{program.result}`")
    return registers[program.result]


def _call(micro_op, filled, table, step, replay):
    """Run the command with its arguments filled in. A replay compares the micro-op as decoded."""
    command, args = micro_op["command"], micro_op["args"]
    if replay is not None:
        if step not in replay or replay[step][:2] != (command, args):
            raise Trap(f"step {step} asked for {command} {args}, which is not what the recording holds")
        return replay[step][2]
    try:
        return table[command].run(**filled) or ""
    except (CommandError, OSError) as error:
        raise Trap(f"step {step} could not run {command}: {error}")


def recording(cycles):
    """What `run` needs to replay a recorded run: step -> (command, args, output)."""
    return {cycle["step"]: (cycle["micro_op"]["command"], cycle["micro_op"]["args"], cycle["output"])
            for cycle in cycles if cycle["micro_op"]["op"] == "call"}
