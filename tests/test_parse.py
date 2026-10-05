import unittest

from los.parse import UsageError, parse, render, usage
from tests.helpers import recording_table

TABLE = recording_table([])


class ParseTest(unittest.TestCase):
    def test_known_command_with_dashed_parameter(self):
        command, args = parse("fs.list --path ~/Downloads --sort-by size", TABLE)
        self.assertEqual((command.name, args), ("fs.list", {"path": "~/Downloads", "sort_by": "size"}))

    def test_equals_form_and_quoted_value(self):
        self.assertEqual(parse("note.add --text='buy oat milk'", TABLE)[1], {"text": "buy oat milk"})
        self.assertEqual(parse('note.add --text "buy oat milk"', TABLE)[1], {"text": "buy oat milk"})

    def test_plain_language_is_not_a_command(self):
        self.assertIsNone(parse("show me what is in this folder", TABLE))
        self.assertIsNone(parse("what's running right now", TABLE))    # unbalanced quote
        self.assertIsNone(parse("", TABLE))

    def test_wrong_use_of_a_known_command(self):
        for line in ("fs.list --colour red", "fs.list --path", "fs.list ~/Downloads"):
            with self.assertRaises(UsageError, msg=line):
                parse(line, TABLE)

    def test_render_reads_back_as_the_same_command(self):
        for args in ({"path": "~/My Files"}, {"text": "it's \"quoted\""}, {"path": ".", "sort_by": "time"}, {}):
            self.assertEqual(parse(render("fs.list" if "text" not in args else "note.add", args), TABLE)[1], args)
        self.assertEqual(render("fs.list", {"sort_by": "size"}), "fs.list --sort-by size")

    def test_usage_lists_parameters(self):
        self.assertEqual(usage(TABLE["fs.move"]), "fs.move [--source VALUE] [--dest VALUE]")
