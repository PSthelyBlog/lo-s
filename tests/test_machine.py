import unittest

from los import machine, plugins, state
from los.models import ModelUnavailable, validate
from los.plugins import Command, CommandError, Program
from tests.helpers import Scripted, ShellCase, StateCase

PROGRAM = Program(("Read the level and store it in `raw`.", "Judge `raw` and store `fine` or `worrying` in `level`.",
                   "Tell the user and store it in `verdict`."), ("raw", "level", "verdict", "place"), ("tank.level",),
                  "verdict")


def micro(**op):
    return {"micro_op": op}


CALL = micro(op="call", command="tank.level", args={"unit": "litres"}, into="raw")
RUN = [CALL, micro(op="set", register="level", value="fine"),
       micro(op="set", register="verdict", value="The tank is fine at 40 litres.")]


def table(read=lambda **args: f"40 {args.get('unit', 'l')}"):
    return {"tank.level": Command("tank.level", "Read the tank level", {"unit": "litres or gallons"}, "read", read)}


class MachineTest(StateCase):
    def test_one_micro_op_per_instruction_and_the_result_register_is_returned(self):
        model = Scripted(*RUN)
        self.assertEqual(machine.run("tank.check", PROGRAM, table(), model), "The tank is fine at 40 litres.")
        self.assertEqual(model.calls, 3)
        cycles = state.read("cycles")
        self.assertEqual([cycle["step"] for cycle in cycles], [1, 2, 3])
        self.assertEqual(len({cycle["run"] for cycle in cycles}), 1)
        self.assertEqual((cycles[0]["output"], cycles[0]["registers"], cycles[1]["registers"]),
                         ("40 litres", {}, {"raw": "40 litres"}))

    def test_the_decoder_sees_one_instruction_the_registers_and_only_the_allowed_commands(self):
        seen = []

        class Watching(Scripted):
            def complete(self, system, user, schema):
                seen.append((system, user))
                return super().complete(system, user, schema)

        machine.run("tank.check", PROGRAM, {**table(), "fs.delete": Command("fs.delete", "Delete", {}, "destructive")},
                    Watching(*RUN), args={"place": "the shed"})
        system, user = seen[1]
        self.assertEqual(user, 'Instruction: Judge `raw` and store `fine` or `worrying` in `level`.\n'
                               'Registers: {"place": "the shed", "raw": "40 litres"}')
        self.assertIn("tank.level | Read the tank level | unit (litres or gallons)", system)
        self.assertNotIn("fs.delete", system)
        self.assertNotIn("Tell the user", system + user)            # the rest of the program is not shown

    def test_schema_limits_commands_parameters_and_registers(self):
        schema = machine.schema(PROGRAM, table())
        for good in (CALL, RUN[1], micro(op="halt"), micro(op="call", command="tank.level", args={}, into="raw")):
            self.assertEqual(validate(good, schema), [], good)
        for bad in (micro(op="call", command="fs.delete", args={}, into="raw"),
                    micro(op="call", command="tank.level", args={"depth": "2"}, into="raw"),
                    micro(op="call", command="tank.level", args={}, into="elsewhere"),
                    micro(op="set", register="elsewhere", value="x"), micro(op="jump", to="1"), {"op": "halt"}):
            self.assertTrue(validate(bad, schema), bad)

    def test_halt_stops_early(self):
        model = Scripted(CALL, micro(op="set", register="verdict", value="Enough."), micro(op="halt"))
        self.assertEqual(machine.run("tank.check", PROGRAM, table(), model), "Enough.")
        self.assertEqual(state.read("cycles")[-1]["micro_op"], {"op": "halt"})

    def test_traps(self):
        with self.assertRaisesRegex(machine.Trap, "without storing anything in `verdict`"):
            machine.run("tank.check", PROGRAM, table(), Scripted(CALL, micro(op="halt")))

        def broken(**args):
            raise CommandError("the sensor is unplugged")

        with self.assertRaisesRegex(machine.Trap, "step 1 could not run tank.level: the sensor is unplugged"):
            machine.run("tank.check", PROGRAM, table(broken), Scripted(*RUN))
        bad = micro(op="set", register="nowhere", value="x")
        with self.assertRaisesRegex(machine.Trap, "step 1: the decoder gave no usable micro-op"):
            machine.run("tank.check", PROGRAM, table(), Scripted(bad, bad, bad))
        with self.assertRaises(ModelUnavailable):
            machine.run("tank.check", PROGRAM, table(), Scripted(ModelUnavailable("down")))

    def test_a_recorded_run_replays_without_running_commands(self):
        cycles = []
        machine.run("tank.check", PROGRAM, table(), Scripted(*RUN), record=cycles.append)
        self.assertEqual(state.read("cycles"), [])                  # a custom recorder replaces the state file
        replay = machine.recording(cycles)
        self.assertEqual(replay, {1: ("tank.level", {"unit": "litres"}, "40 litres")})

        def must_not_run(**args):
            raise AssertionError("the command ran during a replay")

        again = []
        machine.run("tank.check", PROGRAM, table(must_not_run), Scripted(*RUN), replay=replay, record=again.append)
        self.assertEqual(again[1]["registers"], {"raw": "40 litres"})
        other = micro(op="call", command="tank.level", args={"unit": "gallons"}, into="raw")
        with self.assertRaisesRegex(machine.Trap, "not what the recording holds"):
            machine.run("tank.check", PROGRAM, table(), Scripted(other), replay=replay, record=again.append)


class ProgramInTheShellTest(ShellCase):
    def shell_with_program(self, *decodes):
        shell = self.shell()
        shell.table = {**table(), "tank.check": Command("tank.check", "Check the tank", {"place": ""}, "read",
                                                        program=PROGRAM)}
        shell.decoder = Scripted(*decodes)
        return shell

    def test_a_program_runs_like_any_command_and_trace_shows_its_cycles(self):
        shell = self.shell_with_program(*RUN)
        shell.handle("trace")
        self.assertEqual(self.shown[-1], "No program has run yet.")
        shell.handle("tank.check --place shed")
        self.assertEqual(self.shown[-1], "The tank is fine at 40 litres.")
        self.assertEqual((shell.decoder.calls, self.model.calls), (3, 0))
        shell.handle("trace")
        self.assertIn("tank.check: 3 cycle(s)", self.shown[-4])
        self.assertEqual(self.shown[-3], "1. Read the level and store it in `raw`.\n"
                                         "   call tank.level --unit litres into raw  (0.00 s)")
        self.assertIn("   set level = fine", self.shown[-2])

    def test_a_trap_or_a_missing_decoder_is_reported(self):
        shell = self.shell_with_program(CALL, micro(op="halt"))
        shell.handle("tank.check")
        self.assertEqual(self.shown[-1], "tank.check: the program ended without storing anything in `verdict`")
        shell = self.shell_with_program(ModelUnavailable("no answer"))
        shell.handle("tank.check")
        self.assertIn("is a program, and the model that decodes it is unavailable", self.shown[-1])


class StarterProgramTest(unittest.TestCase):
    def test_the_health_program_decodes_against_its_own_schema(self):
        full = plugins.load(state.ROOT / "plugins")
        program = full["sys.health"].program
        schema = machine.schema(program, full)
        self.assertEqual(validate(micro(op="call", command="sys.status", args={"what": "memory"}, into="mem"), schema), [])
        self.assertTrue(validate(micro(op="call", command="fs.move", args={}, into="mem"), schema))
        self.assertIn("sys.status | Show battery, CPU, memory or temperature status", machine.system_prompt(program, full))
