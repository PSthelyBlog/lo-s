from los import machine, rules, state
from los.plugins import Command, Program
from tests.helpers import Scripted, ShellCase, StateCase

JUDGE = "Judge the temperature in `temp`. Store `fine` or `worrying` in `level`."
PROGRAM = Program(("Read the temperature and store it in `temp`.", JUDGE), ("temp", "level"), ("t.read",), "level")
TABLE = {"t.read": Command("t.read", "Read the temperature", {}, "read", lambda: "81 C")}
READINGS = {70: "fine", 74: "fine", 78: "fine", 79: "fine", 84: "worrying", 85: "worrying", 86: "worrying",
            88: "worrying"}
GOOD = '''import re


def rule(registers):
    match = re.fullmatch(r"(\\d+) C", registers.get("temp", ""))
    if not match:
        return None
    degrees = int(match.group(1))
    if degrees <= 79:
        return "fine"
    if degrees >= 84:
        return "worrying"
    return None
'''


MODEL = Scripted()      # stands for the decoder: what matters here is its name


def record(degrees, value, remembered=True, **changes):
    """A recorded cycle and, unless told otherwise, the decode memory holds for the same input."""
    cycle = {"run": "r", "program": "t.check", "step": 2, "instruction": JUDGE, "how": "model",
             "registers": {"temp": f"{degrees} C"}, "seconds": 2.0,
             "micro_op": {"op": "set", "register": "level", "value": value}, **changes}
    state.append("cycles", cycle)
    if remembered:
        key = machine.request(MODEL, PROGRAM, TABLE, cycle["instruction"], cycle["registers"])[3]
        state.append("decodes", {"key": key, "micro_op": cycle["micro_op"], "seconds": 2.0})


def record_all():
    for degrees, value in READINGS.items():
        record(degrees, value)


def write(code=GOOD, reason="The line falls between 79 and 84."):
    return {"decision": "write", "reason": reason, "code": code}


class CasesTest(StateCase):
    def test_cases_are_the_distinct_inputs_recorded_for_this_exact_instruction(self):
        record(74, "worrying", remembered=False)        # recorded under an earlier decoder prompt: not in memory
        record_all()                                    # includes 74 as fine, which is what memory holds
        record(70, "fine")                              # a repeat
        record(60, "fine", instruction="An older wording.")     # another instruction
        record(90, "worrying", remembered=False, how="rule")    # an answer a rule gave, never decoded
        register, listed = rules.cases("t.check", 2, PROGRAM, TABLE, MODEL)
        self.assertEqual((register, len(listed)), ("level", 8))
        self.assertIn(({"temp": "84 C"}, "worrying"), listed)
        self.assertIn(({"temp": "74 C"}, "fine"), listed)
        other = Scripted()
        other.model = "another-decoder"
        with self.assertRaises(rules.Unsuitable):       # nothing on record for a different decoder
            rules.cases("t.check", 2, PROGRAM, TABLE, other)
        shown, held = rules.split(listed)
        self.assertEqual((len(shown), len(held)), (6, 2))
        self.assertEqual({value for _, value in shown}, {"fine", "worrying"})

    def test_records_that_cannot_become_a_rule(self):
        def reason():
            with self.assertRaises(rules.Unsuitable) as caught:
                rules.cases("t.check", 2, PROGRAM, TABLE, MODEL)
            return str(caught.exception)

        record(70, "fine")
        self.assertIn("only 1 different input(s)", reason())
        for degrees in range(60, 70):
            record(degrees, "fine")
        self.assertIn("every recorded answer is the same", reason())
        state.replace("cycles", [])
        state.replace("decodes", [])
        record(70, "x", micro_op={"op": "call", "command": "t.read", "args": {}, "into": "temp"})
        self.assertIn("runs a command", reason())


class RuleCodeTest(StateCase):
    def test_check_reads_the_code(self):
        self.assertEqual(rules.check(GOOD), [])
        for bad in ("import os\n" + GOOD, GOOD + "\nLIMIT = len('x')\n", GOOD.replace("(registers)", "(registers, more)"),
                    GOOD.replace("return None\n    degrees", "return open('x').read()\n    degrees"),
                    GOOD.replace("registers.get", "registers.__class__.get"), "def other(registers):\n    return None\n",
                    "def rule(:\n"):
            self.assertTrue(rules.check(bad), bad[:60])

    def test_a_loaded_rule_has_little_within_reach(self):
        self.assertEqual(rules.load(GOOD)({"temp": "70 C"}), "fine")
        sneaky = "def rule(registers):\n    import os\n    return os.getcwd()\n"
        self.assertIn("ImportError", rules.failures(rules.load(sneaky), [({"temp": "70 C"}, "fine")])[0][0])
        with self.assertRaises(NameError):
            rules.load("def rule(registers):\n    return open\n")({})

    def test_a_rule_must_answer_what_it_was_shown_and_never_contradict_what_was_held_back(self):
        listed = [({"temp": f"{degrees} C"}, value) for degrees, value in READINGS.items()]
        self.assertEqual(rules.failures(rules.load(GOOD), listed), ([], 0))
        low = rules.load(GOOD.replace("<= 79", "<= 75"))                    # 78 and 79 now get None
        wrong, _ = rules.failures(low, listed)
        self.assertEqual(len(wrong), 2)
        self.assertIn("should give 'fine', and the rule gives None", wrong[0])
        shown, held = [case for case in listed if case[0]["temp"] not in ("78 C", "79 C")], listed[2:4]
        self.assertEqual(rules.failures(low, shown, held), ([], 0))         # held back, None is allowed
        self.assertEqual(rules.failures(rules.load(GOOD), shown, held), ([], 2))
        high = rules.load(GOOD.replace("<= 79", "<= 77").replace(">= 84", ">= 78"))
        self.assertEqual(len(rules.failures(high, shown, held)[0]), 2)      # contradicting them is not

    def test_an_installed_rule_answers_what_it_covers_for_its_exact_instruction(self):
        path = rules.install("t.check", 2, JUDGE, "level", GOOD, Scripted(), 8)
        self.assertEqual(rules.answer("t.check", 2, JUDGE, {"temp": "60 C"}), ("level", "fine"))
        self.assertEqual(rules.answer("t.check", 2, JUDGE, {"temp": "95 C"}), ("level", "worrying"))
        for uncovered in ({"temp": "81 C"}, {"temp": "warm"}, {}):
            self.assertIsNone(rules.answer("t.check", 2, JUDGE, uncovered))
        self.assertIsNone(rules.answer("t.check", 2, JUDGE + " Reworded.", {"temp": "60 C"}))
        self.assertIsNone(rules.answer("t.check", 1, JUDGE, {"temp": "60 C"}))
        path.unlink()
        self.assertIsNone(rules.answer("t.check", 2, JUDGE, {"temp": "60 C"}))


class RuleInTheMachineTest(StateCase):
    READ = {"micro_op": {"op": "call", "command": "t.read", "args": {}, "into": "temp"}}
    SET = {"micro_op": {"op": "set", "register": "level", "value": "worrying"}}

    def run_at(self, reading, *decodes, **options):
        model = Scripted(*decodes)
        table = {"t.read": Command("t.read", "Read the temperature", {}, "read", lambda: reading)}
        return machine.run("t.check", PROGRAM, table, model, **options), model.calls

    def test_the_rule_is_asked_first_and_the_model_only_when_it_has_no_answer(self):
        rules.install("t.check", 2, JUDGE, "level", GOOD, Scripted(), 8)
        self.assertEqual(self.run_at("90 C", self.READ), ("worrying", 1))          # the rule answered step 2
        self.assertEqual(state.read("cycles")[-1]["how"], "rule")
        self.assertEqual(self.run_at("81 C", self.SET), ("worrying", 1))           # not covered: the model did
        self.assertEqual(state.read("cycles")[-1]["how"], "model")
        self.assertEqual(self.run_at("60 C", self.READ, self.SET, remember=False), ("worrying", 2))


class RuleCommandTest(ShellCase):
    def shell_for(self, author_says, answers=("y",)):
        shell = self.shell(answers=answers)
        shell.table = {**TABLE, "t.check": Command("t.check", "Check the temperature", {}, "read", program=PROGRAM)}
        shell.author = Scripted(author_says)
        return shell

    def test_a_rule_that_reproduces_every_recorded_answer_is_installed_after_agreement(self):
        record_all()
        shell = self.shell_for(write())
        shell.handle("rule t.check 2")
        self.assertIn("from 6 recorded case(s). 2 more are held back", self.shown[0])
        self.assertIn("def rule(registers):", self.shown[1])
        self.assertIn("Why: The line falls between 79 and 84.", self.shown[1])
        self.assertIn("recorded answer for all 6 cases it was shown. Of the 2 held back, it answers 2 correctly "
                      "and leaves 0 to the model", self.shown[1])
        self.assertIn("Installed.", self.shown[-1])
        shell.handle("rules")
        self.assertIn("t.check step 2: from 8 recorded cases, written by scripted", self.shown[-1])
        shell.decoder = Scripted(RuleInTheMachineTest.READ)
        shell.table["t.read"] = Command("t.read", "Read the temperature", {}, "read", lambda: "91 C")
        shell.handle("t.check")
        self.assertEqual((self.shown[-1], shell.decoder.calls), ("worrying", 1))
        shell.handle("trace")
        self.assertIn("1 by rule", self.shown[-3])
        self.assertIn("set level = worrying  (rule)", self.shown[-1])
        shell.handle("stats")
        self.assertIn("Answered by a rule or a pin: 1", self.shown[-1])

    def test_a_rule_that_contradicts_a_held_back_case_is_refused(self):
        record_all()
        shown, held = rules.split(rules.cases("t.check", 2, PROGRAM, TABLE, MODEL)[1])
        self.assertEqual([case[0]["temp"] for case in held], ["78 C", "86 C"])
        shell = self.shell_for(write(GOOD.replace("<= 79", "<= 74").replace(">= 84", ">= 75")), answers=[])
        shell.handle("rule t.check 2")                                      # it calls 78 worrying
        self.assertIn("Its rule cannot be used", self.shown[-1])
        self.assertIn('{"temp": "78 C"} should give \'fine\', and the rule gives \'worrying\'', self.shown[-1])
        self.assertEqual(rules.installed(), {})

    def test_declines_refusals_and_usage(self):
        record_all()
        shell = self.shell_for(write(), answers=[""])
        shell.handle("rule t.check 2")
        self.assertEqual((self.shown[-1], rules.installed()), ("Not installed.", {}))
        shell = self.shell_for({"decision": "decline", "reason": "The answers contradict each other."}, answers=[])
        shell.handle("rule t.check 2")
        self.assertEqual(self.shown[-1], "It declined: The answers contradict each other.")
        shell = self.shell_for(write("import os\n" + GOOD), answers=[])
        shell.handle("rule t.check 2")
        self.assertIn("a rule may only import math and re", self.shown[-1])
        shell.handle("rule t.check 1")
        self.assertIn("No rule for step 1 of t.check: only 0 different input(s)", self.shown[-1])
        for line in ("rule", "rule t.check", "rule t.check 9", "rule t.read 1", "rule nothing 1"):
            shell.handle(line)
            self.assertIn("Usage: rule PROGRAM STEP", self.shown[-1])
        shell.author = None
        shell.handle("rule t.check 2")
        self.assertIn("No model is set up to write rules", self.shown[-1])
        shell.handle("rules")
        self.assertEqual(self.shown[-1], "No rules are installed.")
        shell.handle("help rule")
        self.assertIn("rule PROGRAM STEP asks the author model", self.shown[-1])
