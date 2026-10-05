import os
import pathlib
import tempfile
import time
import unittest

from los import plugins, state
from los.plugins import CommandError
from tests.helpers import StateCase

TABLE = plugins.load(state.ROOT / "plugins")


def run(command, /, **args):
    return TABLE[command].run(**args)


class FilesTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = pathlib.Path(folder.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "budget-2025.pdf").write_bytes(b"x" * 3000)
        (self.root / "docs" / "notes.txt").write_bytes(b"x" * 10)
        (self.root / "old.log").write_bytes(b"x" * 500)
        long_ago = time.time() - 90 * 86400
        os.utime(self.root / "old.log", (long_ago, long_ago))

    def found(self, **args):
        return [line.split()[-1].rsplit("/", 1)[-1] for line in run("fs.find", path=str(self.root), **args).splitlines()]

    def test_find(self):
        self.assertEqual(self.found(), ["budget-2025.pdf", "old.log", "notes.txt"])    # largest first
        self.assertEqual(self.found(name="*.pdf"), ["budget-2025.pdf"])
        self.assertEqual(self.found(name="budget"), ["budget-2025.pdf"])                # a word matches anywhere
        self.assertEqual(self.found(larger_than="1K"), ["budget-2025.pdf"])
        self.assertEqual(self.found(older_than="30 days"), ["old.log"])
        self.assertEqual(self.found(older_than="1 year"), ["match."])                   # "No files match."

    def test_find_explains_values_it_cannot_read(self):
        for args in ({"larger_than": "huge"}, {"older_than": "a while"}, {"older_than": "3 m"}):
            with self.assertRaises(CommandError, msg=args):
                run("fs.find", path=str(self.root), **args)
        with self.assertRaises(CommandError):
            run("fs.find", path=str(self.root / "missing"))

    def test_list_and_usage(self):
        listing = run("fs.list", path=str(self.root), sort_by="size").splitlines()
        self.assertTrue(listing[-1].endswith("docs/") or listing[0].endswith("old.log"), listing)
        self.assertEqual(len(listing), 2)
        usage = run("fs.usage", path=str(self.root)).splitlines()
        self.assertTrue(usage[0].endswith("(total)"))
        self.assertTrue(usage[1].endswith("/docs"))
        with self.assertRaises(CommandError):
            run("fs.usage", path=str(self.root), depth="two")

    def test_move_renames_moves_into_directories_and_never_overwrites(self):
        run("fs.move", source=str(self.root / "old.log"), dest=str(self.root / "older.log"))
        self.assertTrue((self.root / "older.log").exists())
        run("fs.move", source=str(self.root / "older.log"), dest=str(self.root / "docs"))
        self.assertTrue((self.root / "docs" / "older.log").exists())
        with self.assertRaises(CommandError):
            run("fs.move", source=str(self.root / "docs" / "older.log"), dest=str(self.root / "docs" / "notes.txt"))
        self.assertEqual((self.root / "docs" / "notes.txt").read_bytes(), b"x" * 10)
        for args in ({}, {"source": str(self.root / "nope"), "dest": str(self.root / "x")}):
            with self.assertRaises(CommandError):
                run("fs.move", **args)


class NotesAndStatusTest(StateCase):
    def test_notes(self):
        self.assertEqual(run("note.list"), "No notes.")
        run("note.add", text="the wifi password is on the fridge")
        run("note.add", text="cache dispatch results", tag="work")
        self.assertEqual(len(run("note.list").splitlines()), 2)
        self.assertIn("cache dispatch results  [work]", run("note.list", tag="work"))
        with self.assertRaises(CommandError):
            run("note.add")

    def test_status(self):
        self.assertTrue(run("sys.status", what="memory").startswith("Memory: "))
        self.assertEqual(len(run("sys.status", what="all").splitlines()), len(run("sys.status").splitlines()))
        with self.assertRaises(CommandError):
            run("sys.status", what="mood")
