import unittest

from los import dispatch
from los.models import ModelUnavailable, complete_valid, validate
from tests.helpers import NONE, Scripted, call, recording_table

TABLE = recording_table([])


class DispatchTest(unittest.TestCase):
    def test_schema_allows_only_a_command_s_own_parameters(self):
        schema = dispatch.schema(TABLE)
        self.assertEqual(validate(call("fs.list", path="."), schema), [])
        self.assertEqual(validate(call("fs.list"), schema), [])
        self.assertEqual(validate(NONE, schema), [])
        for wrong in (call("fs.list", text="x"), call("fs.list", **{"*.pdf": "*.pdf"}), call("fs.delete"),
                      {"call": {"command": "fs.list"}}, {"command": "fs.list", "args": {}}):
            self.assertTrue(validate(wrong, schema), wrong)

    def test_prompt_lists_commands_by_name_with_hints(self):
        table = dict(TABLE)
        table["fs.list"] = TABLE["fs.list"].__class__("fs.list", "List", {"path": "a directory", "sort_by": ""})
        rows = dispatch.system_prompt(table).split("parameters\n")[1].splitlines()
        self.assertEqual(rows[0], "fs.list | List | path (a directory), sort_by")
        self.assertEqual([row.split(" | ")[0] for row in rows], sorted(table))

    def test_ask_returns_the_choice(self):
        choice = dispatch.ask(Scripted(call("fs.list", path="~")), TABLE, "what is in my home folder")
        self.assertEqual((choice.command, choice.args), ("fs.list", {"path": "~"}))
        self.assertIsNone(dispatch.ask(Scripted(NONE), TABLE, "order a pizza").command)

    def test_invalid_output_is_asked_again(self):
        model = Scripted(call("fs.list", colour="red"), call("fs.list", path="."))
        self.assertEqual(dispatch.ask(model, TABLE, "list things").args, {"path": "."})
        self.assertEqual(model.calls, 2)

    def test_unreachable_model_is_not_retried(self):
        model = Scripted(ModelUnavailable("down"), NONE)
        with self.assertRaises(ModelUnavailable):
            complete_valid(model, "", "", dispatch.schema(TABLE))
        self.assertEqual(model.calls, 1)
