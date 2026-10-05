import dataclasses
import pathlib
import shutil
import tempfile
import tomllib
import unittest

from los import plugins, state, teach
from los.models import ModelUnavailable
from los.shell import Shell
from tests.helpers import NONE, Scripted, StateCase, call, recording_table

CODE = '''"""Copy files."""
import os
import shutil

from los.plugins import CommandError


def do_copy(source=None, dest=None):
    if not source or not dest:
        raise CommandError("needs --source and --dest")
    if os.path.lexists(dest):
        raise CommandError(f"{dest} already exists; nothing was copied")
    shutil.copy2(source, dest)
    return f"Copied {source} to {dest}"
'''
GOOD = teach.Proposal("There is no command that copies.", False, "fs", "copy", 'Copy a file ("backup")', "write",
                      {"source": "what to copy", "dest": "where the copy goes"}, CODE)


def answer(proposal):
    """A proposal as the author model would return it."""
    return {"decision": "write", "reason": proposal.reason, "command": {
        "plugin": proposal.plugin, "verb": proposal.verb, "description": proposal.description,
        "effect": proposal.effect, "code": proposal.code,
        "params": [{"name": name, "hint": hint} for name, hint in proposal.params.items()]}}


class CheckTest(unittest.TestCase):
    def problems(self, **changes):
        return teach.check(dataclasses.replace(GOOD, **changes), recording_table([]))

    def test_a_sound_proposal_passes(self):
        self.assertEqual(self.problems(), [])

    def test_names_and_effect(self):
        for changes in ({"verb": "list"}, {"plugin": "Fs"}, {"verb": "copy-file"}, {"effect": "maybe"},
                        {"params": {"source": "", "from": ""}}):
            self.assertTrue(self.problems(**changes), changes)

    def test_the_function_must_match_what_is_declared(self):
        for code in (CODE.replace("do_copy", "do_duplicate"), CODE.replace("dest=None", "dest=''"),
                     CODE.replace("source=None, dest=None", "source=None"),
                     CODE.replace("source=None, dest=None", "source=None, dest=None, **more")):
            self.assertTrue(self.problems(code=code), code[:160])
        self.assertEqual(self.problems(code=CODE.replace("source=None, dest=None", "*, dest=None, source=None")), [])

    def test_nothing_may_run_at_import(self):
        for extra in ("HERE = os.getcwd()\n", "print('hello')\n", "class Copier:\n    pass\n",
                      "import functools\n\n@functools.cache\ndef helper():\n    return 1\n",
                      "def helper(when=os.getcwd()):\n    return when\n", "if True:\n    X = 1\n"):
            self.assertTrue(self.problems(code=CODE + "\n" + extra), extra)
        self.assertEqual(self.problems(code=CODE + "\nLIMIT = 2 * 1024**2\nNAMES = {'a': [1, 2]}\n"), [])

    def test_imports_and_dynamic_code_are_restricted(self):
        for extra in ("import requests\n", "import importlib\n", "from los import shell\n", "import los.models\n",
                      "from . import other\n", "def helper():\n    return eval('1')\n",
                      "def helper():\n    import ctypes\n"):
            self.assertTrue(self.problems(code=CODE + "\n" + extra), extra)
        self.assertEqual(self.problems(code=CODE + "\nfrom los import state\nimport urllib.request\n"), [])

    def test_code_that_does_not_parse(self):
        self.assertIn("does not parse", self.problems(code="def do_copy(:\n")[0])

    def test_abilities_are_read_from_the_code(self):
        self.assertEqual(teach.abilities(CODE), (["os", "shutil"], ["reads and writes files"]))
        self.assertEqual(teach.abilities("import json\n"), (["json"], []))
        self.assertIn("runs or signals other programs", teach.abilities("import os\nos.system('ls')\n")[1])
        self.assertIn("uses the network", teach.abilities("import urllib.request\n")[1])
        self.assertIn("keeps records in the lo-s state folder", teach.abilities("from los import state\n")[1])
        self.assertNotIn("keeps records in the lo-s state folder", teach.abilities(CODE)[1])


class AskAndInstallTest(unittest.TestCase):
    def test_ask_reads_the_answer_and_briefs_the_author(self):
        seen = {}

        class Author(Scripted):
            def complete(self, system, user, schema):
                seen.update(system=system, user=user)
                return super().complete(system, user, schema)

        proposal = teach.ask(Author(answer(GOOD)), "make a backup copy of config.yaml", recording_table([]),
                             state.ROOT / "plugins" / "fs")
        self.assertEqual(proposal, GOOD)
        self.assertEqual(seen["user"], "The queued line: make a backup copy of config.yaml")
        self.assertIn("fs.move | Test command fs.move | source, dest", seen["system"])
        self.assertIn("def do_find(", seen["system"])

    def test_decline_and_empty_answer(self):
        declined = teach.ask(Scripted({"decision": "decline", "reason": "It needs a pizzeria."}), "order a pizza", {})
        self.assertEqual((declined.declined, declined.reason), (True, "It needs a pizzeria."))
        with self.assertRaises(RuntimeError):
            teach.ask(Scripted({"decision": "write", "reason": "x"}), "copy a file", {})

    def test_install_writes_a_plugin_that_loads_and_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            where = teach.install(GOOD, folder, 'make a "backup" copy', Scripted())
            self.assertEqual(where, pathlib.Path(folder, "fs.copy"))
            manifest = tomllib.loads((where / "plugin.toml").read_text())
            self.assertEqual(manifest["origin"]["need"], 'make a "backup" copy')
            self.assertEqual(manifest["origin"]["written_by"], "scripted")
            command = plugins.load(folder)["fs.copy"]
            self.assertEqual((command.description, command.effect, command.params), (GOOD.description, "write", GOOD.params))
            pathlib.Path(folder, "a.txt").write_text("hello")
            command.run(source=f"{folder}/a.txt", dest=f"{folder}/b.txt")
            self.assertEqual(pathlib.Path(folder, "b.txt").read_text(), "hello")
            with self.assertRaises(plugins.CommandError):
                command.run(source=f"{folder}/a.txt", dest=f"{folder}/b.txt")


class TeachTest(StateCase):
    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.plugin_dir = pathlib.Path(folder.name, "plugins")
        shutil.copytree(state.ROOT / "plugins", self.plugin_dir, ignore=shutil.ignore_patterns("__pycache__", "fs.copy"))
        state.append("needs", {"line": "order a large pizza", "date": "2026-10-05"})
        state.append("needs", {"line": "make a backup copy of config.yaml", "date": "2026-10-05"})
        state.append("labels", {"line": "how much ram is free", "command": "sys.status", "verdict": "accepted"})
        state.append("labels", {"line": "rename a to b", "command": "fs.move", "verdict": "declined"})

    def shell(self, author_says, *dispatch_says, answers=("y",)):
        self.shown, replies = [], list(answers)
        self.dispatcher, self.author = Scripted(*dispatch_says), Scripted(author_says)
        return Shell(plugins.load(self.plugin_dir), self.dispatcher, lambda question: replies.pop(0),
                     self.shown.append, author=self.author, plugin_dir=self.plugin_dir)

    def waiting(self):
        return [need["line"] for need in state.read("needs")]

    def test_a_good_command_is_installed_after_agreement_and_the_check(self):
        shell = self.shell(answer(GOOD), call("sys.status", what="memory"),
                           call("fs.copy", source="config.yaml", dest="config.yaml.bak"))
        shell.handle("teach 2")
        self.assertIn("def do_copy(", self.shown[1])                # the code is shown before the question
        self.assertIn("reads and writes files", self.shown[1])
        self.assertIn("Installed fs.copy", self.shown[-1])
        self.assertIn("fs.copy", shell.table)
        self.assertEqual(self.waiting(), ["order a large pizza"])
        self.assertEqual(state.read("authored")[0]["command"], "fs.copy")
        self.assertEqual(self.dispatcher.calls, 2)                   # one accepted line replayed, plus the need
        source = self.plugin_dir / "fs" / "plugin.toml"
        shell.handle(f"fs.copy --source {source} --dest {self.plugin_dir}/copy.toml")
        self.assertEqual((self.plugin_dir / "copy.toml").read_text(), source.read_text())

    def test_nothing_is_written_without_agreement(self):
        shell = self.shell(answer(GOOD), answers=[""])
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertEqual((self.dispatcher.calls, len(self.waiting())), (0, 2))
        self.assertIn("Not installed", self.shown[-1])

    def test_it_is_removed_when_an_earlier_line_would_change(self):
        shell = self.shell(answer(GOOD), call("fs.copy", source="ram", dest="free"),
                           call("fs.copy", source="config.yaml", dest="config.yaml.bak"))
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertNotIn("fs.copy", shell.table)
        self.assertEqual(len(self.waiting()), 2)
        self.assertIn('"how much ram is free" used to reach sys.status and would now reach fs.copy', self.shown[-1])

    def test_it_is_removed_when_the_need_does_not_reach_it(self):
        shell = self.shell(answer(GOOD), call("sys.status", what="memory"), NONE)
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertIn("reaches nothing, not fs.copy", self.shown[-1])

    def test_it_is_removed_when_the_check_cannot_run_or_the_module_fails_to_load(self):
        shell = self.shell(answer(GOOD), ModelUnavailable("no answer"))
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertIn("needs the model that reads plain language", self.shown[-1])
        broken = dataclasses.replace(GOOD, code=CODE + "\nRATIO = 1 / 0\n")
        shell = self.shell(answer(broken))
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertIn("does not load", self.shown[-1])
        self.assertEqual(len(self.waiting()), 2)

    def test_decline_and_unusable_proposals_ask_nothing(self):
        shell = self.shell({"decision": "decline", "reason": "It needs a pizzeria."}, answers=[])
        shell.handle("teach 1")
        self.assertIn("It declined: It needs a pizzeria.", self.shown[-1])
        self.assertIn("forget 1 removes it", self.shown[-1])
        shell = self.shell(answer(dataclasses.replace(GOOD, code=CODE + "\nprint('hi')\n")), answers=[])
        shell.handle("teach 2")
        self.assertIn("cannot be installed", self.shown[-1])
        self.assertEqual(len(self.waiting()), 2)

    def test_forget_drops_a_need(self):
        shell = self.shell(answer(GOOD))
        shell.handle("forget 1")
        self.assertEqual((self.waiting(), self.shown[-1]),
                         (["make a backup copy of config.yaml"], "Forgotten: order a large pizza"))
        shell.handle("forget 5")
        self.assertIn("Usage: forget NUMBER", self.shown[-1])
        self.assertEqual(len(self.waiting()), 1)

    def test_usage_and_missing_author(self):
        shell = self.shell(answer(GOOD))
        for line in ("teach", "teach 9", "teach two", "teach 1 2"):
            shell.handle(line)
            self.assertIn("Usage: teach NUMBER", self.shown[-1])
        self.assertEqual(self.author.calls, 0)
        shell.author = None
        shell.handle("teach 1")
        self.assertIn("No model is set up to write commands", self.shown[-1])
