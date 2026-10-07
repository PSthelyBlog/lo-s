import pathlib
import shutil
import tempfile
import tomllib
import unittest

from los import delegate, plugins, state
from los.models import ClaudeCli, ModelUnavailable, OpenAICompat
from los.shell import Shell
from tests.helpers import NONE, Scripted, StateCase, call, recording_table
from tests.test_teach import GOOD, answer

RUN = {"answer": "run", "reason": "fs.list shows what a folder holds.", "command": "fs.list",
       "args": [{"name": "path", "value": "/tmp"}, {"name": "sort-by", "value": "size"}]}
NEED = {"answer": "need", "reason": "Nothing here copies a file.", "example": "make a backup copy of config.yaml",
        "notes": "A user varies the file.\nIt must refuse to overwrite. Only for lines that ask for a copy."}
NOTES = "A user varies the file. It must refuse to overwrite. Only for lines that ask for a copy."
ASK = {"answer": "ask", "reason": "Faster can mean several things.", "question": "What is slow: the answers, or starting up?"}
CANNOT = {"answer": "cannot", "reason": "Ordering food needs a shop's service and an account."}


class AskTest(unittest.TestCase):
    def test_the_author_is_told_how_lo_s_works_what_exists_and_which_models_there_are(self):
        author, student = Scripted(RUN), OpenAICompat("http://127.0.0.1:8080/v1/", "student.gguf")
        advice = delegate.ask(author, "what is in /tmp, biggest first", recording_table([]),
                              {"dispatch": student, "decode": student, "author": author, "label": None})
        self.assertEqual((advice.answer, advice.command, advice.args, advice.reason),
                         ("run", "fs.list", {"path": "/tmp", "sort_by": "size"}, RUN["reason"]))
        self.assertEqual(author.user, "What the user wants: what is in /tmp, biggest first")
        self.assertIn("is queued as a need", author.system)
        self.assertIn("fs.move | Test command fs.move | source, dest", author.system)
        self.assertIn("dispatch and decode: student.gguf, behind the OpenAI chat API at http://127.0.0.1:8080/v1\n"
                      "author: a scripted model", author.system)

    def test_each_kind_of_answer(self):
        need = delegate.ask(Scripted(NEED), "I want backups", {}, {})
        self.assertEqual((need.answer, need.example, need.notes), ("need", NEED["example"], NOTES))    # one line each
        asked = delegate.ask(Scripted(ASK), "make it faster", {}, {})
        self.assertEqual((asked.answer, asked.question), ("ask", ASK["question"]))
        self.assertEqual(delegate.ask(Scripted(CANNOT), "order a pizza", {}, {}),
                         delegate.Advice("cannot", CANNOT["reason"]))

    def test_an_answer_that_cannot_be_sent_is_refused(self):
        for output in ({**RUN, "command": "fs.delete"}, {**RUN, "args": [{"name": "depth", "value": "2"}]},
                       {"answer": "run", "reason": "x"}, {**NEED, "notes": " "}, {"answer": "need", "reason": "x"},
                       {"answer": "ask", "reason": "x"}):
            with self.assertRaises(RuntimeError, msg=output):
                delegate.ask(Scripted(output), "something", recording_table([]), {})

    def test_a_provider_says_how_a_program_reaches_it(self):
        teacher = ClaudeCli("claude-opus-5-5")
        self.assertIn('claude -p --safe-mode --model claude-opus-5-5 --tools ""', teacher.describe())
        self.assertEqual(delegate.setup({"author": teacher, "decode": None}), "author: " + teacher.describe())


class DelegateTest(StateCase):
    def shell(self, *author_says, dispatch_says=(), answers=(), table=None, plugin_dir=None):
        self.ran, self.shown, self.asked, replies = [], [], [], list(answers)
        self.author, self.dispatcher = Scripted(*author_says), Scripted(*dispatch_says)

        def ask(question):
            self.asked.append(question)
            return replies.pop(0)

        return Shell(table or recording_table(self.ran), self.dispatcher, ask, self.shown.append,
                     author=self.author, plugin_dir=plugin_dir)

    def waiting(self):
        return [need["line"] for need in state.read("needs")]

    def test_a_command_that_exists_is_shown_and_runs_on_enter_when_it_only_reads(self):
        self.shell(RUN, answers=[""]).handle("delegate what is in /tmp, biggest first")
        self.assertEqual(self.shown[:2], ["Asking scripted what to send for: what is in /tmp, biggest first",
                                          "fs.list shows what a folder holds.\n→ fs.list --path /tmp --sort-by size"])
        self.assertIn("[Y/n]", self.asked[0])
        self.assertEqual(self.ran, [("fs.list", {"path": "/tmp", "sort_by": "size"})])
        # The student was not asked, and its memory learns nothing from what the author chose.
        self.assertEqual((self.dispatcher.calls, state.read("labels"), state.read("needs")), (0, [], []))
        told = state.read("delegations")[0]
        self.assertEqual((told["wish"], told["answer"], told["command"], told["outcome"], told["model"]),
                         ("what is in /tmp, biggest first", "run", "fs.list", "ran", "scripted"))

    def test_a_command_that_changes_something_needs_an_explicit_yes(self):
        write = {"answer": "run", "reason": "A note keeps it.", "command": "note.add",
                 "args": [{"name": "text", "value": "buy milk"}]}
        self.shell(write, answers=[""]).handle("delegate remember to buy milk")
        self.assertIn("[y/N]", self.asked[0])
        self.assertEqual((self.ran, self.shown[-1], state.read("delegations")[0]["outcome"]), ([], "Not run.", "nothing sent"))
        self.shell(write, answers=["y"]).handle("delegate remember to buy milk")
        self.assertEqual(self.ran, [("note.add", {"text": "buy milk"})])

    def test_a_need_is_queued_with_its_notes_and_teaching_is_offered(self):
        shell = self.shell(NEED, answers=["", ""], plugin_dir="plugins")
        shell.handle("delegate I want backups of my files")
        self.assertEqual(self.shown[1], "Nothing here copies a file.\nThis needs a new command.\n"
                                        f"  Example line: make a backup copy of config.yaml\n  For the author: {NOTES}")
        self.assertEqual(["[Y/n]" in self.asked[0], "Teach it now? [y/N]" in self.asked[1]], [True, True])
        need = state.read("needs")[0]
        self.assertEqual((need["line"], need["notes"], need["wish"], need["decided_by"]),
                         ("make a backup copy of config.yaml", NOTES, "I want backups of my files", "author"))
        self.assertEqual((self.shown[-1], self.author.calls), ("Queued as need 1.", 1))     # not taught
        shell.handle("needs")
        self.assertEqual(self.shown[-1], f"1. make a backup copy of config.yaml  ({need['date']})\n   For the author: {NOTES}")

    def test_declining_to_queue_leaves_the_queue_alone(self):
        self.shell(NEED, answers=["n"]).handle("delegate I want backups of my files")
        self.assertEqual((self.shown[-1], state.read("needs"), len(self.asked)), ("Not queued.", [], 1))

    def test_teaching_at_once_gives_the_author_the_notes_and_checks_the_example_line(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        plugin_dir = pathlib.Path(folder.name, "plugins")
        shutil.copytree(state.ROOT / "plugins", plugin_dir, ignore=shutil.ignore_patterns("__pycache__", "fs.copy"))
        shell = self.shell(NEED, answer(GOOD), dispatch_says=[call("fs.copy", source="config.yaml")],
                           answers=["", "y", "y"], table=plugins.load(plugin_dir), plugin_dir=plugin_dir)
        shell.handle("delegate I want backups of my files")
        self.assertEqual(self.author.user, f"The queued line: make a backup copy of config.yaml\n"
                                           f"Notes on what is wanted: {NOTES}")
        self.assertEqual(self.dispatcher.user, "make a backup copy of config.yaml")
        self.assertIn("Installed fs.copy", self.shown[-1])
        self.assertEqual((self.author.calls, state.read("needs")), (2, []))
        origin = tomllib.loads((plugin_dir / "fs.copy" / "plugin.toml").read_text())["origin"]
        self.assertEqual((origin["need"], origin["notes"]), ("make a backup copy of config.yaml", NOTES))

    def test_a_number_delegates_a_queued_need(self):
        for line in ("show me /tmp", "copy my config", "order a large pizza"):
            state.append("needs", {"line": line, "date": "2026-10-05", "decided_by": "model"})
        shell = self.shell(NEED, RUN, CANNOT, answers=["", ""])
        shell.handle("delegate 2")
        self.assertEqual(self.author.user, "What the user wants: copy my config")
        self.assertEqual((self.waiting(), self.shown[-1]),
                         (["show me /tmp", "make a backup copy of config.yaml", "order a large pizza"],
                          "Queued in place of need 2."))
        shell.handle("delegate 1")      # a command exists after all, so the need is answered
        self.assertEqual(self.ran, [("fs.list", {"path": "/tmp", "sort_by": "size"})])
        self.assertEqual(self.waiting(), ["make a backup copy of config.yaml", "order a large pizza"])
        self.assertIn("That answers need 1, so it left the queue.", self.shown)
        shell.handle("delegate 2")
        self.assertEqual(self.shown[-1], "lo-s cannot do this: Ordering food needs a shop's service and an account.\n"
                                         "The need stays queued; forget 2 removes it.")
        self.assertEqual(len(self.waiting()), 2)

    def test_a_question_or_a_refusal_sends_nothing(self):
        shell = self.shell(ASK, CANNOT)
        shell.handle("delegate make it faster")
        self.assertEqual(self.shown[-1], "Faster can mean several things.\nscripted asks: What is slow: the answers, "
                                         "or starting up?\nType delegate again with the answer included.")
        shell.handle("delegate order a large pizza")
        self.assertEqual(self.shown[-1], "lo-s cannot do this: Ordering food needs a shop's service and an account.")
        self.assertEqual((self.asked, self.ran, state.read("needs")), ([], [], []))
        self.assertEqual([told["outcome"] for told in state.read("delegations")], ["nothing sent"] * 2)

    def test_usage_and_an_author_that_is_missing_or_gives_nothing_usable(self):
        shell = self.shell(ModelUnavailable("the claude program is not installed"), {**RUN, "command": "fs.delete"})
        shell.handle("delegate")
        self.assertIn("Usage: delegate WHAT YOU NEED", self.shown[-1])
        shell.handle("delegate 4")
        self.assertIn("Usage: delegate NUMBER", self.shown[-1])
        self.assertEqual(self.author.calls, 0)
        shell.handle("delegate what is in /tmp")
        self.assertEqual(self.shown[-1], "No answer came back: the claude program is not installed")
        shell.handle("delegate remove /tmp/x")
        self.assertEqual(self.shown[-1], "No answer came back: it named a command that does not exist: 'fs.delete'")
        self.assertEqual((self.asked, self.ran, state.read("delegations")), ([], [], []))
        shell.author = None
        shell.handle("delegate what is in /tmp")
        self.assertIn("No model is set up to delegate to", self.shown[-1])

    def test_a_queued_line_points_at_delegate_when_there_is_an_author(self):
        self.shell(dispatch_says=[NONE]).handle("order a large pizza")
        self.assertEqual(self.shown[-1], "Nothing here does that yet. Queued as a new need (1 waiting).\n"
                                         "delegate 1 asks scripted what to send for it.")
