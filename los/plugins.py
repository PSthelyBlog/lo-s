"""Plugins: which commands exist, what they take and how to run them.

A plugin is a directory holding `plugin.toml`, which declares its commands, and `commands.py`,
which defines one function `do_<verb>` per command. Parameters arrive as keyword strings and the
function returns the text to show. Several directories may add commands under one plugin name;
a command written by a model lives in a directory of its own, so removing it is one deletion.
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
class Command:
    name: str                   # plugin.verb
    description: str
    params: dict                # parameter name -> hint for whoever fills it in, possibly empty
    effect: str = "read"        # read, write or destructive
    run: object = None          # the do_<verb> function


def load(directory):
    """Read every plugin under `directory` into a table of commands keyed by name."""
    table = {}
    for manifest in sorted(pathlib.Path(directory).glob("*/plugin.toml")):
        spec = tomllib.loads(manifest.read_text())
        module_spec = importlib.util.spec_from_file_location(
            "los_plugin_" + manifest.parent.name.replace(".", "_"), manifest.parent / "commands.py")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        for verb, entry in spec["commands"].items():
            name, effect = f"{spec['name']}.{verb}", entry.get("effect", "read")
            if effect not in EFFECTS:
                raise ValueError(f"{name}: effect must be one of {', '.join(EFFECTS)}, not {effect!r}")
            if name in table:
                raise ValueError(f"{name} is defined twice, the second time in {manifest.parent}")
            table[name] = Command(name, entry["description"], dict(entry.get("params", {})), effect,
                                  getattr(module, f"do_{verb}"))
    return table


def version(table):
    """A short fingerprint of the table. It is recorded with every label, because the right
    command for a line changes when commands are added."""
    canon = json.dumps(sorted([c.name, c.description, sorted(c.params)] for c in table.values()))
    return hashlib.sha256(canon.encode()).hexdigest()[:12]
