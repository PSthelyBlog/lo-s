import pathlib
import tempfile
import unittest

from los import plugins, state
from tests.helpers import recording_table


class PluginsTest(unittest.TestCase):
    def test_starter_plugins_load(self):
        table = plugins.load(state.ROOT / "plugins")
        self.assertLessEqual({"fs.find", "fs.list", "fs.move", "fs.usage", "note.add", "note.list", "sys.status"},
                             set(table))
        self.assertEqual((table["fs.move"].effect, table["note.add"].effect, table["fs.find"].effect),
                         ("destructive", "write", "read"))
        self.assertIn("larger_than", table["fs.find"].params)
        self.assertTrue(all(callable(command.run) for command in table.values()))

    def test_version_follows_the_table(self):
        table = recording_table([])
        self.assertEqual(plugins.version(table), plugins.version(dict(reversed(table.items()))))
        smaller = {name: command for name, command in table.items() if name != "fs.move"}
        self.assertNotEqual(plugins.version(table), plugins.version(smaller))

    def test_unknown_effect_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin = pathlib.Path(folder, "x")
            plugin.mkdir()
            (plugin / "plugin.toml").write_text('name = "x"\n[commands.go]\ndescription = "Go"\neffect = "maybe"\n')
            (plugin / "commands.py").write_text("def do_go():\n    return 'gone'\n")
            with self.assertRaises(ValueError):
                plugins.load(folder)
