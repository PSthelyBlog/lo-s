"""Writing a new command for a queued need.

The author model returns text only: a command's description and the source of its module. Nothing
here trusts that text. `check` reads the code without running it, `install` writes it into a
plugin directory of its own, and the shell asks the user before installing and tests the result
afterwards.
"""
import ast
import dataclasses
import datetime
import json
import keyword
import pathlib
import re
import sys

from . import plugins
from .dispatch import table_text
from .models import complete_valid

BRIEF = """\
You write new commands for lo-s, a command-line operating system. A user typed a line that no \
existing command could handle, and it was queued. Write one command that would handle it, or \
decline if a command is the wrong answer.

How commands are used. A command is named plugin.verb and takes named parameters, all optional \
strings. A user either types it directly, as in fs.find --path ~ --name '*.pdf', or types plain \
language. In that case a small local model reads the command table (each command's name, one-line \
description and parameter names with short hints) and picks a command and fills in its \
parameters. That model sees nothing but the table, so the description and hints you write are \
what it relies on. Say plainly what the command does, in words a user would use, and make each \
hint show the form the value takes.

Write a command that is general. The queued line is one example of the need, so give the command \
the parameters a user would reasonably vary and no more. Put it in an existing plugin when it \
belongs with that plugin's commands. When notes come with the line, they say what the line \
leaves out and what this machine has: follow them.

Decline, giving the reason, when an existing command already does this (name it), when a program \
on this machine cannot do it because it needs a service, an account or hardware that is not \
there, or when it is not something a command should do.

The code. Give the full source of a Python module that defines do_<verb>.
- do_<verb> takes exactly the parameters you declare, as keyword arguments that default to None. \
The values arrive as strings. It returns the text to show the user.
- When it cannot do what was asked, for instance a missing parameter, a value it cannot read or a \
file that is not there, it raises CommandError with a message the user can act on. Import it \
with: from los.plugins import CommandError
- The module stands alone. It may import the standard library, CommandError as above, and \
`from los import state`, where state.append(name, record) and state.read(name) keep JSON records \
between runs. It cannot import another plugin. Helper functions and plain constants are fine. \
Nothing may run when the module is imported: no classes, no decorators, no calls at the top level.
- It does not use eval, exec, compile, __import__, importlib or ctypes.
- It never loses data silently. It refuses to overwrite or delete unless that is the stated \
purpose of the command.

The effect tells the shell how careful to be. "read" changes nothing. "write" creates or changes \
something. "destructive" can lose data: it deletes, overwrites or stops something. Choose the \
most cautious one that applies.

The user reads your code before it is installed, so keep it short and plain.
"""

TEXT = {"type": "string"}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["decision", "reason"],
    "properties": {
        "decision": {"type": "string", "enum": ["write", "decline"]},
        "reason": TEXT,     # why this command, or why none
        "command": {
            "type": "object", "additionalProperties": False,
            "required": ["plugin", "verb", "description", "effect", "params", "code"],
            "properties": {
                "plugin": TEXT, "verb": TEXT, "description": TEXT,
                "effect": {"type": "string", "enum": list(plugins.EFFECTS)},
                "params": {"type": "array", "items": {
                    "type": "object", "additionalProperties": False, "required": ["name", "hint"],
                    "properties": {"name": TEXT, "hint": TEXT}}},
                "code": TEXT,
            },
        },
    },
}

FORBIDDEN_NAMES = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_MODULES = {"importlib", "ctypes"}
# What a module's imports let it do, shown to the user before they agree to install it.
ABILITIES = {
    "reads and writes files": {"os", "shutil", "pathlib", "glob", "tempfile", "io", "fileinput", "zipfile", "tarfile"},
    "uses the network": {"socket", "urllib", "http", "ftplib", "smtplib", "ssl", "imaplib", "poplib", "xmlrpc"},
    "runs or signals other programs": {"subprocess", "signal", "pty", "multiprocessing"},
}
RUNS_PROGRAMS = re.compile(r"^(system|popen|kill|killpg|exec\w*|spawn\w*|posix_spawn\w*|fork\w*)$")


@dataclasses.dataclass
class Proposal:
    reason: str
    declined: bool = False
    plugin: str = ""
    verb: str = ""
    description: str = ""
    effect: str = ""
    params: dict = dataclasses.field(default_factory=dict)      # parameter name -> hint
    code: str = ""

    @property
    def name(self):
        return f"{self.plugin}.{self.verb}"


def ask(author, line, table, example=None, notes=None):
    """Ask the author model for a command that would handle the queued line. `notes` say what
    the line leaves out, when the need was queued through `delegate`."""
    system = BRIEF + "\nExisting commands, one per line as: name | what it does | parameters\n" + table_text(table)
    example = pathlib.Path(example) if example else None
    if example and (example / "commands.py").exists():
        system += (f"\n\nAn existing plugin, as an example of the style.\n\nplugin.toml:\n"
                   f"{(example / 'plugin.toml').read_text()}\ncommands.py:\n{(example / 'commands.py').read_text()}")
    user = f"The queued line: {line}" + (f"\nNotes on what is wanted: {notes}" if notes else "")
    output, _ = complete_valid(author, system, user, SCHEMA)
    if output["decision"] == "decline":
        return Proposal(output["reason"], declined=True)
    if "command" not in output:
        raise RuntimeError("the author chose to write a command but returned none")
    command = output["command"]
    return Proposal(output["reason"], False, command["plugin"], command["verb"], command["description"],
                    command["effect"], {param["name"]: param["hint"] for param in command["params"]},
                    command["code"])


def check(proposal, table):
    """Read a proposal without running any of it. Returns the reasons it cannot be installed."""
    problems = []
    if not re.fullmatch(r"[a-z][a-z0-9]*", proposal.plugin):
        problems.append(f"plugin name {proposal.plugin!r} must be lower-case letters and digits")
    if not re.fullmatch(r"[a-z][a-z0-9_]*", proposal.verb):
        problems.append(f"verb {proposal.verb!r} must be lower-case letters, digits and underscores")
    if proposal.name in table:
        problems.append(f"{proposal.name} already exists")
    if proposal.effect not in plugins.EFFECTS:
        problems.append(f"effect {proposal.effect!r} is not one of {', '.join(plugins.EFFECTS)}")
    for name in proposal.params:
        if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or keyword.iskeyword(name):
            problems.append(f"parameter name {name!r} cannot be used")
    try:
        tree = ast.parse(proposal.code)
    except SyntaxError as error:
        return problems + [f"the code does not parse: {error.msg} on line {error.lineno}"]
    return problems + _code_problems(tree, proposal)


def _code_problems(tree, proposal):
    problems, entry = [], None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.decorator_list or _calls(node.args):
                problems.append(f"{node.name} would run code when the module is imported")
            if node.name == f"do_{proposal.verb}":
                entry = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is not None and _calls(node.value):
                problems.append(f"line {node.lineno} would run code when the module is imported")
        elif not (isinstance(node, (ast.Import, ast.ImportFrom))
                  or (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))):
            problems.append(f"line {node.lineno}: only imports, functions and plain constants may be at the top level")

    if entry is None:
        problems.append(f"the code does not define do_{proposal.verb}")
    else:
        args = entry.args
        names = [arg.arg for arg in args.args + args.kwonlyargs]
        defaults = [None] * (len(args.args) - len(args.defaults)) + args.defaults + args.kw_defaults
        if args.posonlyargs or args.vararg or args.kwarg or sorted(names) != sorted(proposal.params):
            problems.append(f"do_{proposal.verb} must take exactly the declared parameters: "
                            f"{', '.join(proposal.params) or 'none'}")
        elif not all(isinstance(default, ast.Constant) and default.value is None for default in defaults):
            problems.append(f"every parameter of do_{proposal.verb} must default to None")

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"line {node.lineno} uses {node.id}, which is not allowed")
    for module, names, line in _imports(tree):
        top = module.split(".")[0]
        if top in FORBIDDEN_MODULES:
            problems.append(f"line {line} imports {module}, which is not allowed")
        elif top == "los":
            if (module, names) not in (("los.plugins", ("CommandError",)), ("los", ("state",))):
                problems.append(f"line {line}: from lo-s itself only CommandError and state may be imported")
        elif top not in sys.stdlib_module_names:
            problems.append(f"line {line} imports {module}, which is not in the standard library")
    return problems


def _calls(node):
    return any(isinstance(inner, (ast.Call, ast.Await, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp,
                                  ast.GeneratorExp)) for inner in ast.walk(node))


def _imports(tree):
    """Every import in the code as (module, imported names, line)."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, (), node.lineno
        elif isinstance(node, ast.ImportFrom):
            yield "." * node.level + (node.module or ""), tuple(alias.name for alias in node.names), node.lineno


def abilities(code):
    """What the code's imports let it do, in the user's terms. A guide for the reader, not a guarantee."""
    tree = ast.parse(code)
    modules = {module.split(".")[0] for module, _, _ in _imports(tree)}
    found = [ability for ability, group in ABILITIES.items() if modules & group]
    uses_os_to_run = any(isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                         and node.value.id == "os" and RUNS_PROGRAMS.match(node.attr) for node in ast.walk(tree))
    if uses_os_to_run and "runs or signals other programs" not in found:
        found.append("runs or signals other programs")
    if any(module == "los" and "state" in names for module, names, _ in _imports(tree)):
        found.append("keeps records in the lo-s state folder")
    return sorted(modules - {"los"}), found


def render(proposal):
    """The proposal as the user sees it before deciding."""
    width = max(map(len, proposal.params), default=0)
    modules, found = abilities(proposal.code)
    lines = [f"{proposal.name}  {proposal.description}  (effect: {proposal.effect})"]
    lines += [f"  --{name.replace('_', '-'):{width}}  {hint}" for name, hint in proposal.params.items()]
    lines += [f"Why: {proposal.reason}",
              f"Imports: {', '.join(modules) or 'nothing'}. It {', '.join(found) or 'only computes'}.",
              "", proposal.code.rstrip(), ""]
    return "\n".join(lines)


def install(proposal, plugin_dir, line, author, notes=None):
    """Write the proposal as a plugin directory of its own. Returns the directory."""
    folder = pathlib.Path(plugin_dir) / proposal.name
    folder.mkdir()
    quoted = json.dumps     # a JSON string is also a valid TOML string
    manifest = [f"name = {quoted(proposal.plugin)}", "",
                "[origin]", f"need = {quoted(line)}", *([f"notes = {quoted(notes)}"] if notes else []),
                f"written_by = {quoted(author.model)}",
                f"provider = {quoted(author.provider)}", f"date = {quoted(datetime.date.today().isoformat())}", "",
                f"[commands.{proposal.verb}]", f"description = {quoted(proposal.description)}",
                f"effect = {quoted(proposal.effect)}", f"[commands.{proposal.verb}.params]"]
    manifest += [f"{name} = {quoted(hint)}" for name, hint in proposal.params.items()]
    (folder / "plugin.toml").write_text("\n".join(manifest) + "\n")
    (folder / "commands.py").write_text(proposal.code.rstrip() + "\n")
    return folder
