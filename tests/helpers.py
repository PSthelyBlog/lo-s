"""Shared test fixtures."""
import os
import tempfile
import unittest

from los.plugins import Command
from los.shell import Shell


class Scripted:
    """A model that replays prepared outputs instead of thinking."""

    provider = model = "scripted"

    def __init__(self, *outputs):
        self.outputs, self.calls = list(outputs), 0

    def describe(self):
        return "a scripted model"

    def complete(self, system, user, schema):
        self.calls += 1
        self.system, self.user = system, user       # what it was last asked
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return output, {"seconds": 0.0}


def call(command, **args):
    return {"call": {"command": command, "args": args}}


NONE = {"call": {"command": "none"}}


def recording_table(ran):
    """Three commands, one per effect, that note what they were called with."""
    def command(name, params, effect):
        return Command(name, f"Test command {name}", dict.fromkeys(params, ""), effect,
                       lambda **args: ran.append((name, args)) or f"ran {name}")
    commands = [command("fs.list", ["path", "sort_by"], "read"), command("note.add", ["text"], "write"),
                command("fs.move", ["source", "dest"], "destructive")]
    return {c.name: c for c in commands}


class StateCase(unittest.TestCase):
    """Gives each test an empty state directory."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        previous = os.environ.get("LOS_STATE")
        os.environ["LOS_STATE"] = folder.name
        self.addCleanup(lambda: os.environ.pop("LOS_STATE") if previous is None
                        else os.environ.__setitem__("LOS_STATE", previous))


class ShellCase(StateCase):
    """Adds a shell whose commands, model and user are all scripted."""

    def shell(self, *model_outputs, answers=()):
        """A shell over the recording table, a scripted model and scripted answers to its questions."""
        self.ran, self.shown, self.asked = [], [], []
        self.model = Scripted(*model_outputs)
        replies = list(answers)

        def ask(question):
            self.asked.append(question)
            if not replies:
                raise EOFError
            return replies.pop(0)

        return Shell(recording_table(self.ran), self.model, ask, self.shown.append)
