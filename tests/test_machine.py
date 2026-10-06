import unittest

from los import machine, plugins, state
from los.models import ModelUnavailable, validate
from los.plugins import Command, CommandError, Program
from tests.helpers import Scripted, ShellCase, StateCase

PROGRAM = Program(("Read the level and store it in `raw`.", "Judge `raw` and store `fine` or `worrying` in `level`.",
                   "Tell the user the `level`, quoting `raw`, and store it in `verdict`."),
                  ("raw", "level", "verdict", "place"), ("tank.level",),
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
                               'Registers: {"raw": "40 litres"}')     # `place` is set but not named, so not shown
        self.assertEqual(seen[0][1], 'Instruction: Read the level and store it in `raw`.\nRegisters: {}')
        self.assertIn("tank.level | Read the tank level | unit (litres or gallons)", system)
        self.assertNotIn("fs.delete", system)
        self.assertNotIn("Tell the user", system + user)            # the rest of the program is not shown

    def test_a_decode_seen_before_is_answered_from_memory(self):
        first, again = Scripted(*RUN), Scripted()
        machine.run("tank.check", PROGRAM, table(), first)
        self.assertEqual(machine.run("tank.check", PROGRAM, table(), again), "The tank is fine at 40 litres.")
        self.assertEqual((first.calls, again.calls), (3, 0))            # the second run never asks the model
        cycles = state.read("cycles")
        self.assertEqual([cycle["how"] for cycle in cycles], ["model"] * 3 + ["memory"] * 3)
        self.assertEqual(len(state.read("decodes")), 3)

    def test_memory_misses_when_what_the_decoder_sees_changes(self):
        machine.run("tank.check", PROGRAM, table(), Scripted(*RUN))
        # A different reading: step 1 names no register that is set, so it is remembered. Steps 2
        # and 3 name `raw`, which now holds something else, so they go to the model.
        fuller = Scripted(RUN[1], RUN[2])
        machine.run("tank.check", PROGRAM, table(lambda **args: "90 litres"), fuller)
        self.assertEqual(fuller.calls, 2)
        self.assertEqual([cycle["how"] for cycle in state.read("cycles")[3:]], ["memory", "model", "model"])
        # Another model, the same model asked with other settings, or memory switched off, asks
        # again from the start.
        other, asked_differently = Scripted(*RUN), Scripted(*RUN)
        other.model, asked_differently.extra = "another-model", {"cache_prompt": False}
        machine.run("tank.check", PROGRAM, table(), other)
        machine.run("tank.check", PROGRAM, table(), asked_differently)
        unremembered = Scripted(*RUN)
        machine.run("tank.check", PROGRAM, table(), unremembered, remember=False)
        self.assertEqual((other.calls, asked_differently.calls, unremembered.calls), (3, 3, 3))

    def test_a_register_in_braces_is_filled_in_by_the_machine_and_not_shown_to_the_decoder(self):
        program = Program(("Read the level and store it in `raw`.",
                           "Read the level again in the unit named by {place} and store it in `level`.",
                           "Tell the user the reading, quoting it as {raw} and {level}, and store it in `verdict`."),
                          ("raw", "level", "verdict", "place"), ("tank.level",), "verdict")
        seen, asked_for = [], []

        class Watching(Scripted):
            def complete(self, system, user, schema):
                seen.append(user)
                return super().complete(system, user, schema)

        def read(**args):
            asked_for.append(args)
            return f"40 {args.get('unit', 'l')}"

        decodes = [micro(op="call", command="tank.level", args={}, into="raw"),
                   micro(op="call", command="tank.level", args={"unit": "{place}"}, into="level"),
                   micro(op="set", register="verdict", value="It holds {raw}, or {level}; {braces} stay.")]
        result = machine.run("tank.check", program, table(read), Watching(*decodes), args={"place": "gallons"})
        self.assertEqual(result, "It holds 40 l, or 40 gallons; {braces} stay.")
        self.assertEqual(asked_for[1], {"unit": "gallons"})                 # the argument was filled in
        self.assertTrue(all(user.endswith("Registers: {}") for user in seen), seen)   # nothing was shown
        self.assertEqual(state.read("cycles")[2]["micro_op"]["value"], "It holds {raw}, or {level}; {braces} stay.")
        with self.assertRaisesRegex(machine.Trap, "step 1 refers to {level}, which holds nothing yet"):
            machine.run("tank.check", program, table(read), Scripted(micro(op="set", register="raw", value="{level}")),
                        remember=False)

    def test_a_pinned_input_is_never_sent_to_the_decoder(self):
        pinned = Program(PROGRAM.instructions[:2], PROGRAM.registers, PROGRAM.calls, "level",
                         pins=((2, {"raw": "40 litres"}, {"op": "set", "register": "level", "value": "fine"}),))
        model = Scripted(CALL)
        self.assertEqual(machine.run("tank.check", pinned, table(), model), "fine")
        self.assertEqual((model.calls, [cycle["how"] for cycle in state.read("cycles")]), (1, ["model", "pinned"]))
        other = Scripted(RUN[1])                        # another reading is not pinned, so the model is asked
        machine.run("tank.check", pinned, table(lambda **args: "90 litres"), other)
        self.assertEqual((other.calls, state.read("cycles")[-1]["how"]), (1, "model"))
        every = Scripted(CALL, RUN[1])                  # with memory off, the pin is not used either
        machine.run("tank.check", pinned, table(), every, remember=False)
        self.assertEqual(every.calls, 2)

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
        def run(read, *decodes):       # memory is off, so each case starts from nothing
            return machine.run("tank.check", PROGRAM, table(read) if read else table(), Scripted(*decodes),
                               remember=False)

        with self.assertRaisesRegex(machine.Trap, "without storing anything in `verdict`"):
            run(None, CALL, micro(op="halt"))

        def broken(**args):
            raise CommandError("the sensor is unplugged")

        with self.assertRaisesRegex(machine.Trap, "step 1 could not run tank.level: the sensor is unplugged"):
            run(broken, *RUN)
        bad = micro(op="set", register="nowhere", value="x")
        with self.assertRaisesRegex(machine.Trap, "step 1: the decoder gave no usable micro-op"):
            run(None, bad, bad, bad)
        with self.assertRaises(ModelUnavailable):
            run(None, ModelUnavailable("down"))

    def test_a_recorded_run_replays_without_running_commands(self):
        cycles = []
        machine.run("tank.check", PROGRAM, table(), Scripted(*RUN), record=cycles.append, remember=False)
        self.assertEqual((state.read("cycles"), state.read("decodes")), ([], []))   # nothing went to the state folder
        replay = machine.recording(cycles)
        self.assertEqual(replay, {1: ("tank.level", {"unit": "litres"}, "40 litres")})

        def must_not_run(**args):
            raise AssertionError("the command ran during a replay")

        again = []
        machine.run("tank.check", PROGRAM, table(must_not_run), Scripted(*RUN), replay=replay, record=again.append,
                    remember=False)
        self.assertEqual(again[1]["registers"], {"raw": "40 litres"})
        other = micro(op="call", command="tank.level", args={"unit": "gallons"}, into="raw")
        with self.assertRaisesRegex(machine.Trap, "not what the recording holds"):
            machine.run("tank.check", PROGRAM, table(), Scripted(other), replay=replay, record=again.append,
                        remember=False)


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
        self.assertEqual(self.shown[-4], "tank.check: 3 cycle(s), 0.0 s in the decoder")
        self.assertEqual(self.shown[-3], "1. Read the level and store it in `raw`.\n"
                                         "   call tank.level --unit litres into raw  (0.00 s)")
        self.assertIn("   set level = fine", self.shown[-2])
        shell.handle("tank.check --place shed")                         # every decode is now remembered
        self.assertEqual(shell.decoder.calls, 3)
        shell.handle("trace")
        self.assertEqual(self.shown[-4], "tank.check: 3 cycle(s), 0.0 s in the decoder, 3 from memory")
        self.assertIn("into raw  (remembered)", self.shown[-3])
        shell.handle("stats")
        self.assertIn("Program steps: 6\nDecoded by the model: 3, taking 0.0 s\nAnswered from memory: 3", self.shown[-1])

    def test_a_trap_or_a_missing_decoder_is_reported(self):
        shell = self.shell_with_program(CALL, micro(op="halt"))
        shell.handle("tank.check")
        self.assertEqual(self.shown[-1], "tank.check: the program ended without storing anything in `verdict`")
        state.replace("decodes", [])
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
