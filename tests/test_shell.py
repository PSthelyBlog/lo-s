from los import plugins, state
from los.models import ModelUnavailable
from tests.helpers import NONE, ShellCase, call


class ShellTest(ShellCase):
    def test_structured_command_runs_without_the_model(self):
        self.shell().handle("fs.list --path /tmp")
        self.assertEqual((self.ran, self.model.calls, self.asked), ([("fs.list", {"path": "/tmp"})], 0, []))
        self.assertEqual(self.shown, ["ran fs.list"])

    def test_typed_destructive_command_needs_an_explicit_yes(self):
        self.shell(answers=[""]).handle("fs.move --source a --dest b")
        self.assertEqual(self.ran, [])
        self.assertIn("[y/N]", self.asked[0])
        self.shell(answers=["y"]).handle("fs.move --source a --dest b")
        self.assertEqual(self.ran, [("fs.move", {"source": "a", "dest": "b"})])

    def test_plain_language_shows_the_typed_form_then_runs_on_enter_when_only_reading(self):
        shell = self.shell(call("fs.list", path="~", sort_by="size"), answers=[""])
        shell.handle("what are the biggest things in my home folder")
        self.assertEqual(self.shown[0], "→ fs.list --path ~ --sort-by size")
        self.assertIn("[Y/n]", self.asked[0])
        self.assertEqual(self.ran, [("fs.list", {"path": "~", "sort_by": "size"})])
        label = state.read("labels")[0]
        self.assertEqual((label["verdict"], label["command"], label["table"], label["line"]),
                         ("accepted", "fs.list", plugins.version(shell.table),
                          "what are the biggest things in my home folder"))

    def test_plain_language_that_changes_state_is_not_run_on_enter(self):
        self.shell(call("note.add", text="milk"), answers=["", ""]).handle("jot down milk")
        self.assertEqual(self.ran, [])
        self.assertIn("[y/N]", self.asked[0])
        self.assertEqual(self.shown[-1], "Not run.")
        self.assertEqual(state.read("labels")[0]["verdict"], "declined")
        self.assertEqual(state.read("needs"), [])

    def test_a_rejected_choice_can_be_queued_as_a_need(self):
        self.shell(call("fs.move", source="tmp", dest="tmp2"), answers=["n", "y"]).handle("delete the tmp directory")
        self.assertEqual(self.ran, [])
        need = state.read("needs")[0]
        self.assertEqual((need["line"], need["decided_by"]), ("delete the tmp directory", "user"))
        self.assertEqual(state.read("labels")[0]["verdict"], "wrong")

    def test_nothing_fits_is_queued(self):
        shell = self.shell(NONE, NONE)
        shell.handle("order a large pizza")
        shell.handle("book a flight")
        self.assertEqual([need["decided_by"] for need in state.read("needs")], ["model", "model"])
        self.assertIn("(2 waiting)", self.shown[-1])
        self.assertEqual((self.ran, self.asked, state.read("labels")), ([], [], []))
        shell.needs()
        self.assertIn("1. order a large pizza", self.shown[-2])

    def test_unreachable_model_leaves_structured_commands_working(self):
        shell = self.shell(ModelUnavailable("no answer from http://127.0.0.1:8080/v1"))
        shell.handle("what is in this folder")
        self.assertIn("unavailable", self.shown[0])
        self.assertEqual((self.ran, state.read("needs"), state.read("labels")), ([], [], []))
        shell.handle("fs.list")
        self.assertEqual(self.ran, [("fs.list", {})])

    def test_nobody_to_ask_means_no(self):
        self.shell(call("fs.list")).handle("list it")       # the question hits end of input
        self.assertEqual(self.ran, [])

    def test_wrong_use_shows_how_to_type_it(self):
        self.shell().handle("fs.move --from a")
        self.assertIn("Usage: fs.move [--source VALUE] [--dest VALUE]", self.shown[0])
        self.assertEqual(self.model.calls, 0)

    def test_builtins(self):
        shell = self.shell()
        self.assertFalse(shell.handle("exit"))
        self.assertTrue(shell.handle("   "))
        shell.handle("help")
        self.assertIn("fs.move", self.shown[0])
        shell.handle("help fs.move nope")
        self.assertEqual(self.shown[-2], "  --source  \n  --dest    ")       # hints line up in a column
        self.assertEqual(self.shown[-1], "There is no command named nope.")
        self.assertEqual(self.model.calls, 0)
