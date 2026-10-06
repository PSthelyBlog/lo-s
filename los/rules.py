"""Rules: a step's remembered judgements turned into a small function.

When the decode model has stored a value for the same instruction many times, the record of what
it was shown and what it stored can be handed to the author model, which writes a pure function
giving the same answers. Once the user approves it, the machine asks the rule first and the
decode model only when the rule returns None.

Nothing here trusts the function. `check` reads it without running it, `load` runs it with a few
builtins and two importable modules, and `failures` tries it on every recorded case, including
cases the author was never shown. A rule may leave a held-back case to the model by returning
None, which is always safe. It may never contradict one.
"""
import ast
import builtins
import datetime
import json

from . import state
from .models import complete_valid
from .teach import FORBIDDEN_NAMES, _calls, _imports

MINIMUM = 6                     # different recorded inputs needed before a rule is attempted
IMPORTS = {"re", "math"}        # all a rule may import
SAFE = {name: getattr(builtins, name) for name in (
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "isinstance", "len", "list", "max", "min",
    "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip",
    "Exception", "IndexError", "KeyError", "TypeError", "ValueError")}

BRIEF = """\
A small machine runs programs one instruction at a time. For one instruction, a local language \
model has been deciding which value to store, and the machine has recorded what the model was \
shown and what it stored each time. Write a rule, a small pure Python function, that gives the \
same answers, so that the machine can use the rule in place of the model.

You are given the instruction and the recorded cases. Some recorded cases are held back and your \
rule is tested on those too, so write the rule the cases point to and not a table of the cases.

The function is:  def rule(registers):
- registers is a dict from register name to the text that register holds, exactly as in the cases.
- It returns the value to store, as a string. It returns None whenever the input is not one the \
rule clearly covers: a reading in a form the cases do not show, a missing register, or a value in \
a range where the cases do not settle the answer. The machine then asks the model as before, so \
None is always safe and a wrong answer is not.
- It may import re and math and nothing else. It is a pure function of its argument: no files, \
no network, no state.
- Keep it short and plain. A person reads it before it is used.

Also judge the recorded answers themselves. In your reason, say where the model seems to draw \
its line and whether that looks sensible for what the instruction asks. If the answers \
contradict the instruction or each other too much for any rule, decline.
"""

TEXT = {"type": "string"}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["decision", "reason"],
          "properties": {"decision": {"type": "string", "enum": ["write", "decline"]}, "reason": TEXT, "code": TEXT}}


class Unsuitable(Exception):
    """This step's record cannot become a rule. The message says why."""


def cases(name, step, program):
    """The recorded cases for one step, as (register stored into, [(what the decoder was shown, value)])."""
    from . import machine   # imported here because machine imports this module

    instruction = program.instructions[step - 1]
    found, register = {}, None
    for cycle in state.read("cycles"):
        if (cycle["program"], cycle["step"], cycle["instruction"]) != (name, step, instruction) \
                or cycle.get("how") == "rule":
            continue
        op = cycle["micro_op"]
        if op["op"] != "set":
            raise Unsuitable("this step runs a command, and only a step that stores a value the model worked out "
                             "can become a rule")
        if register not in (None, op["register"]):
            raise Unsuitable("this step has stored into more than one register")
        register = op["register"]
        shown = machine.visible(instruction, program, cycle["registers"])
        key = json.dumps(shown, sort_keys=True)
        if key in found and found[key][1] != op["value"]:
            raise Unsuitable("the record holds two different answers for the same input")
        found[key] = (shown, op["value"])
    listed = [found[key] for key in sorted(found)]
    if len(listed) < MINIMUM:
        raise Unsuitable(f"only {len(listed)} different input(s) are on record for it, and a rule needs {MINIMUM}")
    if len({value for _, value in listed}) < 2:
        raise Unsuitable("every recorded answer is the same, so there is no line for a rule to draw")
    return register, listed


def split(listed):
    """(cases the author is shown, cases held back to test the rule). About a quarter are held back."""
    shown = [case for index, case in enumerate(listed) if index % 4 != 2]
    held = [case for index, case in enumerate(listed) if index % 4 == 2]
    if len({value for _, value in shown}) < 2:
        return listed, []
    return shown, held


def ask(author, instruction, register, shown):
    """Ask the author model for a rule. Returns (code or None when it declines, its reason)."""
    lines = "\n".join(f"{json.dumps(registers, ensure_ascii=False)} => {value}" for registers, value in shown)
    user = f"Instruction: {instruction}\nThe value is stored in: {register}\n\nRecorded cases:\n{lines}"
    output, _ = complete_valid(author, BRIEF, user, SCHEMA)
    if output["decision"] == "decline":
        return None, output["reason"]
    if not output.get("code"):
        raise RuntimeError("the author chose to write a rule but returned none")
    return output["code"], output["reason"]


def check(code):
    """Read a rule without running it. Returns the reasons it cannot be used."""
    try:
        tree = ast.parse(code)
    except SyntaxError as error:
        return [f"the code does not parse: {error.msg} on line {error.lineno}"]
    problems, entry = [], None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.decorator_list or _calls(node.args):
                problems.append(f"{node.name} would run code when the rule is loaded")
            entry = node if node.name == "rule" else entry
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is not None and _calls(node.value):
                problems.append(f"line {node.lineno} would run code when the rule is loaded")
        elif not (isinstance(node, (ast.Import, ast.ImportFrom))
                  or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))):
            problems.append(f"line {node.lineno}: only imports, functions and plain constants may be at the top level")
    if entry is None:
        problems.append("the code does not define rule")
    elif len(entry.args.args) != 1 or entry.args.posonlyargs or entry.args.kwonlyargs or entry.args.vararg \
            or entry.args.kwarg or entry.args.defaults:
        problems.append("rule must take exactly one argument, the registers")
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES | {"open", "input", "globals", "locals", "vars"}:
            problems.append(f"line {node.lineno} uses {node.id}, which is not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            problems.append(f"line {node.lineno} reaches into {node.attr}, which is not allowed")
    for module, _, line in _imports(tree):
        if module.split(".")[0] not in IMPORTS:
            problems.append(f"line {line} imports {module}; a rule may only import {' and '.join(sorted(IMPORTS))}")
    return problems


def load(code):
    """The rule function, defined with only a few builtins and two importable modules in reach."""
    def limited_import(name, *args, **kwargs):
        if name.split(".")[0] not in IMPORTS:
            raise ImportError(f"a rule may not import {name}")
        return __import__(name, *args, **kwargs)

    scope = {"__builtins__": {**SAFE, "__import__": limited_import}}
    exec(compile(code, "<rule>", "exec"), scope)    # the code has passed check() and the user reads it
    return scope["rule"]


def failures(function, shown, held=()):
    """What the rule gets wrong, as sentences, and how many held-back cases it answered.

    It must give the recorded answer for every case its author was shown. For a held-back case
    it may give the recorded answer or None, which leaves the case to the model.
    """
    wrong, answered = [], 0
    for registers, value, may_abstain in [(*case, False) for case in shown] + [(*case, True) for case in held]:
        try:
            got = function(dict(registers))
        except Exception as error:      # whatever the rule does wrong counts against it
            got = f"an error ({type(error).__name__}: {error})"
        if got is None and may_abstain:
            continue
        if got != value:
            wrong.append(f"{json.dumps(registers, ensure_ascii=False)} should give {value!r}, and the rule gives {got!r}")
        answered += may_abstain
    return wrong, answered


def install(name, step, instruction, register, code, author, count):
    """Keep an approved rule. Returns the file it is in."""
    folder = state.directory() / "rules"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}.{step}.py"
    path.write_text(code.rstrip() + "\n")
    state.append("rules", {"program": name, "step": step, "instruction": instruction, "register": register,
                           "file": path.name, "cases": count, "date": datetime.date.today().isoformat(),
                           "written_by": author.model, "provider": author.provider})
    return path


def installed():
    """The rule in force for each (program, step): the latest one whose file still exists."""
    current = {}
    for record in state.read("rules"):
        if (state.directory() / "rules" / record["file"]).exists():
            current[(record["program"], record["step"])] = record
    return current


def answer(name, step, instruction, shown):
    """What an installed rule says for this input: (register, value), or None when there is no
    rule for this exact instruction or the rule does not cover the input."""
    record = installed().get((name, step))
    if not record or record["instruction"] != instruction:
        return None
    try:
        value = load((state.directory() / "rules" / record["file"]).read_text())(dict(shown))
    except Exception:       # a rule that fails simply does not answer; the model is asked instead
        return None
    return (record["register"], value) if isinstance(value, str) else None
