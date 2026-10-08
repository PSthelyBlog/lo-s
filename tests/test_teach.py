import dataclasses
import pathlib
import shutil
import tempfile
import tomllib
import unittest

from los import memory, plugins, state, teach
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

    def test_revise_changes_the_words_and_nothing_else(self):
        author = Scripted({"decision": "reword", "reason": "Narrower.", "description": "Copy one file.",
                           "params": [{"name": "dest", "hint": "the new name"}]})
        revised = teach.revise(author, GOOD, "make a backup copy", recording_table([]),
                               [("rename a to b", "fs.move", "fs.copy")], [("make a backup copy", "fs.copy", None)],
                               notes="Only single files.")
        self.assertEqual(revised, dataclasses.replace(GOOD, reason="Narrower.", description="Copy one file.",
                                                      params={"source": "what to copy", "dest": "the new name"}))
        self.assertTrue(author.user.startswith("The queued line: make a backup copy\n"
                                               "Notes on what is wanted: Only single files.\n"))
        self.assertIn('- "make a backup copy": this is the line of the need and has to reach fs.copy. '
                      "The local model picks nothing.", author.user)
        self.assertIn("Its effect is write.", author.user)
        declined = teach.revise(Scripted({"decision": "decline", "reason": "Ambiguous."}), GOOD, "x", {}, [], [])
        self.assertEqual((declined.declined, declined.reason, declined.code), (True, "Ambiguous.", CODE))

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
        self.before = plugins.version(plugins.load(self.plugin_dir))
        state.append("labels", {"line": "how much ram is free", "command": "sys.status", "args": {"what": "memory"},
                                "verdict": "accepted", "table": self.before, "seconds": 1.5})
        state.append("labels", {"line": "rename a to b", "command": "fs.move", "args": {}, "verdict": "declined",
                                "table": self.before})
        state.append("labels", {"line": "an old line", "command": "fs.list", "args": {}, "verdict": "accepted",
                                "table": "an-earlier-table"})

    def shell(self, author_says, *dispatch_says, answers=("y",)):
        """A shell whose author gives one answer, or each answer of a list in turn."""
        self.shown, self.asked, replies = [], [], list(answers)
        self.dispatcher = Scripted(*dispatch_says)
        self.author = Scripted(*author_says) if isinstance(author_says, list) else Scripted(author_says)

        def ask(question):
            self.asked.append(question)
            if not replies:
                raise EOFError
            return replies.pop(0)

        return Shell(plugins.load(self.plugin_dir), self.dispatcher, ask, self.shown.append,
                     author=self.author, plugin_dir=self.plugin_dir)

    def settle(self, line, command):
        state.append("labels", {"line": line, "command": command, "args": {}, "verdict": "accepted",
                                "table": self.before})

    def text(self):
        return "\n".join(self.shown)

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
        self.assertEqual(self.dispatcher.calls, 2)                   # one settled line replayed, plus the need
        # The settled line is carried to the new table, so it is still answered without the model.
        carried = state.read("labels")[-1]
        self.assertEqual((carried["line"], carried["table"], carried["carried_from"]),
                         ("how much ram is free", shell.table_version, self.before))
        shell.handle("how much ram is free")
        self.assertEqual(self.dispatcher.calls, 2)
        self.assertTrue(self.shown[-1].startswith("Memory: "))
        source = self.plugin_dir / "fs" / "plugin.toml"
        shell.handle(f"fs.copy --source {source} --dest {self.plugin_dir}/copy.toml")
        self.assertEqual((self.plugin_dir / "copy.toml").read_text(), source.read_text())

    def test_nothing_is_written_without_agreement(self):
        shell = self.shell(answer(GOOD), answers=[""])
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertEqual((self.dispatcher.calls, len(self.waiting())), (0, 2))
        self.assertIn("Not installed", self.shown[-1])

    def test_a_failed_check_asks_before_removing(self):
        shell = self.shell(answer(GOOD), call("fs.copy", source="ram", dest="free"),
                           call("fs.copy", source="config.yaml", dest="config.yaml.bak"), answers=["y", ""])
        shell.handle("teach 2")
        self.assertIn('fs.copy did not pass the acceptance check:\n'
                      '  - "how much ram is free" used to reach sys.status and would now reach fs.copy', self.text())
        self.assertEqual(self.shown[-2], "  r      have scripted reword its description, then check again\n"
                                         "  k      keep it, and stop remembering that line\n"
                                         "  Enter  remove it again")
        self.assertEqual(self.asked[-1], "[r/k/Enter] ")
        self.assertEqual(self.shown[-1], "fs.copy was removed again. The need stays queued.")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertNotIn("fs.copy", shell.table)
        self.assertEqual((len(self.waiting()), state.read("authored")), (2, []))

    def test_nobody_there_to_choose_removes_it(self):
        shell = self.shell(answer(GOOD), call("fs.copy"), call("fs.copy"))      # the questions run out
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())

    def test_an_interrupted_check_leaves_nothing_installed(self):
        shell = self.shell(answer(GOOD), call("fs.copy"), call("fs.copy"))
        scripted = shell.ask

        def ask(question):      # agrees to install, then presses Ctrl-C at the choice
            if question.startswith("Install"):
                return scripted(question)
            raise KeyboardInterrupt

        shell.ask = ask
        with self.assertRaises(KeyboardInterrupt):
            shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertEqual((len(self.waiting()), state.read("authored")), (2, []))

    def test_it_can_stay_when_the_lines_that_moved_are_no_longer_remembered(self):
        self.settle("what is in my home", "fs.list")
        shell = self.shell(answer(GOOD), call("fs.copy", source="ram"), call("fs.list", path="~"),
                           call("fs.copy", source="config.yaml"), call("sys.status"), answers=["y", "k"])
        shell.handle("teach 2")
        self.assertEqual(self.shown[-2], 'No longer remembered: "how much ram is free". '
                                         "The model is asked the next time you type it.")
        self.assertIn("Installed fs.copy", self.shown[-1])
        self.assertEqual(self.waiting(), ["order a large pizza"])
        # The line that still reached its command is carried. The other stays settled for the old table only.
        self.assertEqual(list(memory.recall(shell.table_version)), ["what is in my home"])
        self.assertIn("how much ram is free", memory.recall(self.before))
        self.assertEqual(state.read("authored")[0]["forgotten"], ["how much ram is free"])
        self.assertEqual(self.dispatcher.calls, 3)
        shell.handle("how much ram is free")
        self.assertEqual(self.dispatcher.calls, 4)                   # asked again, not answered from memory

    def test_the_author_can_reword_it_and_the_check_runs_again(self):
        reworded = {"decision": "reword", "reason": "It said too little about what it is not for.",
                    "description": "Copy a file. Not for memory or disk figures, which are sys.status.",
                    "params": [{"name": "dest", "hint": "where the copy goes, such as notes.bak"}]}
        shell = self.shell([answer(GOOD), reworded], call("fs.copy", source="ram"), call("fs.copy"),
                           call("sys.status", what="memory"), call("fs.copy", source="config.yaml"),
                           answers=["y", "r", ""])
        shell.handle("teach 2")
        self.assertIn("Asking scripted to reword fs.copy.", self.shown)
        self.assertIn("fs.copy  Copy a file. Not for memory or disk figures, which are sys.status.  (effect: write)\n"
                      "  --source  what to copy\n  --dest    where the copy goes, such as notes.bak\n"
                      "Why: It said too little about what it is not for.\nThe code stays as you read it.", self.shown)
        self.assertEqual(self.asked[-1], "Check it again with this description? [Y/n] ")
        self.assertIn("Installed fs.copy", self.shown[-1])
        # The author was told its command, the line that went astray and where it went.
        self.assertIn("fs.copy | Copy a file (\"backup\") | source (what to copy), dest (where the copy goes)",
                      self.author.user)
        self.assertIn("def do_copy(", self.author.user)
        self.assertIn('- "how much ram is free": the user accepted sys.status for this line. With your command in '
                      "the table the local model picks fs.copy.", self.author.user)
        self.assertIn("sys.status | ", self.author.system)
        self.assertNotIn("fs.copy | ", self.author.system)
        # What is installed is the new wording around the code the user read.
        command = shell.table["fs.copy"]
        self.assertEqual((command.description, command.params["dest"], command.params["source"]),
                         (reworded["description"], "where the copy goes, such as notes.bak", "what to copy"))
        self.assertEqual((self.plugin_dir / "fs.copy" / "commands.py").read_text(), CODE)
        self.assertEqual((self.dispatcher.calls, state.read("authored")[0]["reworded"]), (4, 1))
        self.assertEqual(list(memory.recall(shell.table_version)), ["how much ram is free"])

    def test_a_second_rewording_is_told_what_was_tried(self):
        first = {"decision": "reword", "reason": "x", "description": "Copy a file, nothing else."}
        declined = {"decision": "decline", "reason": "The line means either command now."}
        shell = self.shell([answer(GOOD), first, declined], call("fs.copy"), call("fs.copy"), call("fs.copy"),
                           call("fs.copy"), answers=["y", "r", "", "r", "k"])
        shell.handle("teach 2")
        self.assertIn('A description tried before, which failed the check too: Copy a file ("backup")\n'
                      "What the check found with the description it has now:", self.author.user)
        self.assertIn("fs.copy | Copy a file, nothing else. | ", self.author.user)
        self.assertEqual(shell.table["fs.copy"].description, "Copy a file, nothing else.")
        self.assertEqual((state.read("authored")[0]["reworded"], self.dispatcher.calls), (1, 4))

    def test_a_rewording_that_is_refused_or_unusable_leaves_the_choice_open(self):
        declined = {"decision": "decline", "reason": "The line means either command now."}
        nothing_new = {"decision": "reword", "reason": "x", "description": GOOD.description}
        stray_hint = {"decision": "reword", "reason": "x", "description": "Copy.", "params": [{"name": "to", "hint": ""}]}
        fine = {"decision": "reword", "reason": "x", "description": "Copy a file."}
        shell = self.shell([answer(GOOD), declined, nothing_new, stray_hint, fine],
                           call("fs.copy"), call("fs.copy"), answers=["y", "r", "r", "r", "r", "n", "k"])
        shell.handle("teach 2")
        text = self.text()
        self.assertIn("It declined: The line means either command now.", text)
        self.assertIn("No new wording came back: it changed neither the description nor a hint", text)
        self.assertIn("No new wording came back: it gave a hint for a parameter fs.copy does not have: to", text)
        self.assertIn("fs.copy keeps the description it had.", text)
        self.assertEqual(self.dispatcher.calls, 2)                   # nothing changed, so nothing was checked again
        self.assertEqual(shell.table["fs.copy"].description, GOOD.description)
        self.assertNotIn("reworded", state.read("authored")[0])

    def test_it_cannot_stay_when_the_need_does_not_reach_it(self):
        shell = self.shell(answer(GOOD), call("sys.status", what="memory"), NONE, answers=["y", "k"])
        shell.handle("teach 2")
        self.assertIn('  - "make a backup copy of config.yaml" reaches nothing, not fs.copy', self.text())
        self.assertEqual(self.asked[-1], "[r/Enter] ")               # keeping it is not offered
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertEqual(len(self.waiting()), 2)

    def test_a_check_that_could_not_be_made_can_be_tried_again(self):
        shell = self.shell(answer(GOOD), ModelUnavailable("no answer"), call("sys.status", what="memory"),
                           call("fs.copy", source="config.yaml"), answers=["y", "t"])
        shell.handle("teach 2")
        self.assertIn("  - the check needs the model that reads plain language: no answer", self.text())
        self.assertIn("  t      try the check again\n  Enter  remove it again", self.shown)
        self.assertIn("Installed fs.copy", self.shown[-1])
        self.assertEqual((self.author.calls, self.dispatcher.calls), (1, 3))

    def test_a_check_that_could_not_be_made_does_not_offer_to_keep_it(self):
        shell = self.shell(answer(GOOD), call("fs.copy"), ModelUnavailable("no answer"), answers=["y", "k"])
        shell.handle("teach 2")
        self.assertEqual(self.asked[-1], "[t/Enter] ")
        self.assertEqual(self.shown[-1], "fs.copy was removed again. The need stays queued.")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())

    def test_a_module_that_does_not_load_is_removed_without_a_question(self):
        broken = dataclasses.replace(GOOD, code=CODE + "\nRATIO = 1 / 0\n")
        shell = self.shell(answer(broken))
        shell.handle("teach 2")
        self.assertFalse((self.plugin_dir / "fs.copy").exists())
        self.assertIn("  - it does not load: division by zero", self.shown[-2])
        self.assertEqual((len(self.asked), len(self.waiting())), (1, 2))

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
