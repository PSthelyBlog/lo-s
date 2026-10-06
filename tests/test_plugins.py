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
        self.assertTrue(all(callable(command.run) or command.program for command in table.values()))
        health = table["sys.health"]
        self.assertEqual((health.run, health.program.calls, health.program.result, len(health.program.instructions)),
                         (None, ("sys.status",), "verdict", 5))
        self.assertEqual([step for step, _, _ in health.program.pins], [5, 5, 5, 5])
        self.assertIn(({"mem_level": "fine", "temp_level": "worrying"},
                       {"op": "set", "register": "verdict", "value": "Worrying: {temp}"}),
                      [(shown, op) for _, shown, op in health.program.pins])

    def test_version_follows_the_table(self):
        table = recording_table([])
        self.assertEqual(plugins.version(table), plugins.version(dict(reversed(table.items()))))
        smaller = {name: command for name, command in table.items() if name != "fs.move"}
        self.assertNotEqual(plugins.version(table), plugins.version(smaller))

    def program_plugin(self, folder, **changes):
        entry = {"description": '"Check"', "effect": '"read"', "calls": '["a.read"]', "registers": '["x", "out"]',
                 "result": '"out"', "program": '["Read it into `x`.", "Sum it up in `out`."]', **changes}
        for name, effect in (("a", "read"), ("b", "write")):
            plugin = pathlib.Path(folder, name)
            plugin.mkdir(exist_ok=True)
            (plugin / "plugin.toml").write_text(f'name = "{name}"\n[commands.read]\ndescription = "R"\n'
                                                f'effect = "{effect}"\n')
            (plugin / "commands.py").write_text("def do_read():\n    return 'data'\n")
        plugin = pathlib.Path(folder, "p")
        plugin.mkdir(exist_ok=True)
        (plugin / "plugin.toml").write_text('name = "p"\n[commands.check]\n' +
                                            "".join(f"{key} = {value}\n" for key, value in entry.items()))
        return plugins.load(folder)

    def test_a_program_needs_no_code_and_is_checked_when_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            command = self.program_plugin(folder)["p.check"]
            self.assertEqual(command.program.instructions, ("Read it into `x`.", "Sum it up in `out`."))
            for changes in ({"calls": '["a.missing"]'}, {"calls": '["p.check"]'}, {"result": '"other"'},
                            {"calls": '["b.read"]'}):          # b.read writes; the program says it only reads
                with self.assertRaises(ValueError, msg=changes):
                    self.program_plugin(folder, **changes)
            self.assertIn("p.check", self.program_plugin(folder, calls='["b.read"]', effect='"write"'))
            good = '[{step = 2, registers = {x = "1"}, micro_op = {op = "set", register = "out", value = "one"}}]'
            self.assertEqual(len(self.program_plugin(folder, pins=good)["p.check"].program.pins), 1)
            for bad in (good.replace("step = 2", "step = 9"), good.replace('"out"', '"elsewhere"'),
                        good.replace('"set"', '"halt"'), good.replace("{x =", "{y =")):
                with self.assertRaises(ValueError, msg=bad):
                    self.program_plugin(folder, pins=bad)

    def test_unknown_effect_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            plugin = pathlib.Path(folder, "x")
            plugin.mkdir()
            (plugin / "plugin.toml").write_text('name = "x"\n[commands.go]\ndescription = "Go"\neffect = "maybe"\n')
            (plugin / "commands.py").write_text("def do_go():\n    return 'gone'\n")
            with self.assertRaises(ValueError):
                plugins.load(folder)
