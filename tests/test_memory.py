from los import memory, plugins, state
from tests.helpers import NONE, ShellCase, StateCase, call


class RecallTest(StateCase):
    def label(self, line, verdict, table="t1", command="fs.list"):
        state.append("labels", {"line": line, "command": command, "args": {}, "verdict": verdict, "table": table})

    def test_only_accepted_lines_of_this_table_are_settled(self):
        self.label("list it", "accepted")
        self.label("jot it", "declined")
        self.label("other table", "accepted", table="t2")
        state.append("labels", {"line": "old wording", "command": "fs.list", "verdict": "rejected"})
        self.assertEqual(list(memory.recall("t1")), ["list it"])
        self.assertEqual(list(memory.recall("t2")), ["other table"])

    def test_wrong_takes_a_line_back_and_accepting_again_restores_it(self):
        self.label("list it", "accepted")
        self.label("list it", "wrong")
        self.assertEqual(memory.recall("t1"), {})
        self.label("list it", "accepted", command="fs.move")
        self.assertEqual(memory.recall("t1")["list it"]["command"], "fs.move")

    def test_carry_stamps_settled_lines_for_the_new_table(self):
        self.label("list it", "accepted")
        self.label("jot it", "declined")
        memory.carry("t1", "t2")
        self.assertEqual(list(memory.recall("t2")), ["list it"])
        self.assertEqual(memory.recall("t2")["list it"]["carried_from"], "t1")

    def test_carry_can_leave_lines_settled_for_the_old_table_only(self):
        self.label("list it", "accepted")
        self.label("show it", "accepted")
        memory.carry("t1", "t2", leave_out=["show it"])
        self.assertEqual((list(memory.recall("t1")), list(memory.recall("t2"))), (["list it", "show it"], ["list it"]))


class RememberedLinesTest(ShellCase):
    def test_an_accepted_reading_line_is_answered_from_memory_without_asking(self):
        shell = self.shell(call("fs.list", path="~"), answers=[""])
        shell.handle("what is in my home folder")
        shell.handle("what is in my home folder")
        self.assertEqual((self.model.calls, len(self.asked)), (1, 1))
        self.assertEqual(self.ran, [("fs.list", {"path": "~"})] * 2)
        self.assertEqual(self.shown[-2], "→ fs.list --path ~  (remembered)")
        self.assertEqual(len(state.read("labels")), 1)             # remembering adds no new label

    def test_a_remembered_line_that_changes_something_still_asks(self):
        shell = self.shell(call("note.add", text="milk"), answers=["y", "y", ""])
        shell.handle("jot down milk")
        shell.handle("jot down milk")
        self.assertEqual((self.model.calls, len(self.ran)), (1, 2))
        self.assertIn("[y/N]", self.asked[1])
        shell.handle("jot down milk")                               # Enter: not run, and not queued either
        self.assertEqual((self.model.calls, len(self.ran), self.shown[-1]), (1, 2, "Not run."))
        self.assertEqual(len(state.read("labels")), 1)

    def test_declined_lines_are_not_remembered(self):
        shell = self.shell(call("note.add", text="milk"), call("note.add", text="milk"), answers=["", "", "y"])
        shell.handle("jot down milk")
        shell.handle("jot down milk")
        self.assertEqual(self.model.calls, 2)

    def test_a_different_table_does_not_remember(self):
        self.shell(call("fs.list"), answers=[""]).handle("list it")
        shell = self.shell(call("fs.list"), answers=[""])
        shell.table = {name: command for name, command in shell.table.items() if name != "fs.move"}
        shell.table_version = plugins.version(shell.table)
        shell.handle("list it")
        self.assertEqual(self.model.calls, 1)

    def test_wrong_takes_the_latest_choice_back(self):
        shell = self.shell(call("fs.list"), call("fs.list"), answers=["", ""])
        shell.handle("wrong")
        self.assertEqual(self.shown[-1], "There is no plain-language choice to take back.")
        shell.handle("show the big files")
        shell.handle("wrong")
        self.assertIn('"show the big files" is no longer remembered as fs.list', self.shown[-1])
        shell.handle("show the big files")
        self.assertEqual(self.model.calls, 2)                       # asked again

    def test_queuing_a_remembered_choice_as_a_need_marks_it_wrong(self):
        shell = self.shell(call("note.add", text="x"), NONE, answers=["y", "n", "y"])
        shell.handle("jot down x")
        shell.handle("jot down x")                                  # remembered, declined, queued
        self.assertEqual([label["verdict"] for label in state.read("labels")], ["accepted", "wrong"])
        shell.handle("jot down x")
        self.assertEqual(self.model.calls, 2)

    def test_stats_count_what_memory_saved(self):
        shell = self.shell(call("fs.list"), NONE, answers=[""])
        shell.model.complete = lambda *_: (call("fs.list"), {"seconds": 1.25}) if not self.ran else (NONE, {"seconds": 0.5})
        shell.handle("list it")
        shell.handle("list it")
        shell.handle("list it")
        shell.handle("order a pizza")
        shell.handle("stats")
        self.assertEqual(self.shown[-1], "Plain-language lines: 4\n"
                                         "Answered by the model: 2, taking 1.8 s\n"
                                         "Answered from memory: 2, saving about 2.5 s")
