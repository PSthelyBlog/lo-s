"""The shell: read a line, run it as a command, or find out which command was meant.

A line goes through three steps. A structured command runs directly, with no model involved.
Anything else goes to the dispatch model, whose choice is shown in typed form and run only if
the user agrees. If the model finds no fitting command, the line is queued as a new need, and
`teach` asks the author model to write a command for it.

A line the user has accepted before is answered from memory instead of by the model, for as long
as the command table stays the same.
"""
import argparse
import datetime
import os
import pathlib
import shutil
import tomllib

from . import dispatch, machine, memory, plugins, rules, state, teach
from .models import ModelUnavailable, provider
from .parse import UsageError, parse, render, usage


BUILTINS = {
    "help": "help lists the commands. help NAME explains one.",
    "needs": "needs lists the lines no command could handle, numbered.",
    "teach": "teach NUMBER asks the author model to write a command for a queued need. You see it before it is installed.",
    "forget": "forget NUMBER drops a queued need.",
    "wrong": "wrong takes back the latest plain-language choice, so that line is asked about again.",
    "stats": "stats shows how many lines and program steps the model, memory and rules answered.",
    "trace": "trace shows each step of the latest program run and the micro-op it became.",
    "rule": "rule PROGRAM STEP asks the author model to turn a step's remembered answers into a small function. "
            "The step must have stored at least six different inputs with two different answers.",
    "rules": "rules lists the rules in use.",
    "exit": "exit leaves the shell.",
}


class Shell:
    def __init__(self, table, model, ask=input, out=print, author=None, plugin_dir=None, decoder=None):
        self.table, self.model, self.ask, self.out = table, model, ask, out
        self.author, self.plugin_dir = author, plugin_dir
        self.decoder = decoder or model     # decodes a program's instructions into micro-ops
        self.table_version = plugins.version(table)
        self.last = None    # the latest plain-language choice, so that `wrong` can take it back

    def handle(self, line):
        """Take one typed line through the three steps. Returns False when the shell should stop."""
        line = line.strip()
        words = line.split()
        if not words:
            return True
        if line in ("exit", "quit"):
            return False
        if words[0] == "help":
            self.help(words[1:])
        elif line == "needs":
            self.needs()
        elif words[0] == "teach":
            self.teach(words[1:])
        elif words[0] == "forget":
            self.forget(words[1:])
        elif line == "wrong":
            self.wrong()
        elif line == "stats":
            self.stats()
        elif line == "trace":
            self.trace()
        elif words[0] == "rule":
            self.rule(words[1:])
        elif line == "rules":
            self.list_rules()
        else:
            try:
                parsed = parse(line, self.table)
            except UsageError as error:
                self.out(f"{error}\nUsage: {usage(self.table[words[0]])}")
                return True
            if parsed:
                self.run_typed(*parsed)
            else:
                self.interpret(line)
        return True

    def run_typed(self, command, args):
        if command.effect == "destructive" and not self.confirm(
                f"{command.name} makes changes that cannot be undone. Run it?", default=False):
            self.out("Not run.")
            return
        self.run(command, args)

    def interpret(self, line):
        """Plain language. A line the user settled before is answered from memory; otherwise the
        model is asked. Either way the choice is shown as a typed command before anything runs."""
        record = {"date": datetime.date.today().isoformat(), "line": line, "table": self.table_version}
        remembered = memory.recall(self.table_version).get(line)
        if remembered and remembered["command"] in self.table:
            command, args = self.table[remembered["command"]], remembered["args"]
            state.append("dispatches", {**record, "how": "memory", "seconds": remembered.get("seconds") or 0})
            self.out("→ " + render(command.name, args) + "  (remembered)")
            # The user agreed to this before, so reading runs at once. Changing anything still asks.
            accepted = command.effect == "read" or self.confirm("Run it?", default=False)
        else:
            remembered = None
            try:
                choice = dispatch.ask(self.model, self.table, line)
            except ModelUnavailable as error:
                self.out(f"That is not a command, and the model that reads plain language is unavailable: {error}.\n"
                         "Structured commands still work; type help to list them.")
                return
            except RuntimeError as error:
                self.out(f"The model's answer could not be used: {error}")
                return
            record.update(provider=self.model.provider, model=self.model.model, seconds=choice.meta.get("seconds"))
            state.append("dispatches", {**record, "how": "model"})
            if choice.command is None:
                self.queue(record, "model")
                return
            command, args = self.table[choice.command], choice.args
            self.out("→ " + render(command.name, args))
            # Reading is safe to accept with Enter; anything that changes state needs an explicit yes.
            accepted = self.confirm("Run it?", default=command.effect == "read")

        # The user's answer is a label for this line against this version of the table. Accepting
        # settles it. Declining does not: the choice may be right and simply unwanted just now.
        # Queuing it as a need instead says the choice was wrong.
        self.last = {**record, "command": command.name, "args": args}
        if accepted:
            if not remembered:
                state.append("labels", {**self.last, "verdict": "accepted"})
            self.run(command, args)
        elif self.confirm("Queue it as a new need instead?", default=False):
            state.append("labels", {**self.last, "verdict": "wrong"})
            self.queue(record, "user")
        else:
            if not remembered:
                state.append("labels", {**self.last, "verdict": "declined"})
            self.out("Not run.")

    def wrong(self):
        """Take back the latest plain-language choice, so the line is no longer answered from memory."""
        if not self.last:
            self.out("There is no plain-language choice to take back.")
            return
        state.append("labels", {**self.last, "verdict": "wrong"})
        self.out(f"Taken back: \"{self.last['line']}\" is no longer remembered as {self.last['command']}.")
        self.last = None

    def trace(self):
        """Show the cycles of the latest program run: each instruction and the micro-op it became."""
        cycles = state.read("cycles")
        if not cycles:
            self.out("No program has run yet.")
            return
        latest = [cycle for cycle in cycles if cycle["run"] == cycles[-1]["run"]]
        total = sum(cycle["seconds"] or 0 for cycle in latest)
        recalled = sum(cycle.get("how") == "memory" for cycle in latest)
        ruled = sum(cycle.get("how") == "rule" for cycle in latest)
        self.out(f"{latest[0]['program']}: {len(latest)} cycle(s), {total:.1f} s in the decoder" +
                 (f", {recalled} from memory" if recalled else "") + (f", {ruled} by rule" if ruled else ""))
        for cycle in latest:
            op = cycle["micro_op"]
            if op["op"] == "call":
                did = f"call {render(op['command'], op['args'])} into {op['into']}"
            elif op["op"] == "set":
                did = f"set {op['register']} = {op['value']}"
            else:
                did = "halt"
            took = {"memory": "remembered", "rule": "rule", "pinned": "pinned"}.get(cycle.get("how")) \
                or f"{cycle['seconds'] or 0:.2f} s"
            self.out(f"{cycle['step']}. {cycle['instruction']}\n   {did}  ({took})")

    def rule(self, words):
        """Turn one program step's recorded answers into a rule, if the author model writes one
        that reproduces them all and the user agrees to it."""
        command = self.table.get(words[0]) if len(words) == 2 else None
        if not command or not command.program or not words[1].isdigit() \
                or not 1 <= int(words[1]) <= len(command.program.instructions):
            self.out("Usage: rule PROGRAM STEP, for a step of a command written as a program, numbered as in trace.")
            return
        if not self.author:
            self.out("No model is set up to write rules. Give the author role a provider in los.toml.")
            return
        name, step, program = command.name, int(words[1]), command.program
        try:
            register, listed = rules.cases(name, step, program, self.table, self.decoder)
        except rules.Unsuitable as reason:
            self.out(f"No rule for step {step} of {name}: {reason}.")
            return
        shown, held = rules.split(listed)
        self.out(f"Asking {self.author.model} for a rule from {len(shown)} recorded case(s). "
                 f"{len(held)} more are held back to test it.")
        try:
            code, reason = rules.ask(self.author, program.instructions[step - 1], register, shown)
        except (ModelUnavailable, RuntimeError) as error:
            self.out(f"No rule came back: {error}")
            return
        if code is None:
            self.out(f"It declined: {reason}")
            return
        problems, answered = rules.check(code), 0
        if not problems:
            try:
                problems, answered = rules.failures(rules.load(code), shown, held)
            except Exception as error:      # anything the code does wrong while being defined
                problems = [f"it does not load: {error}"]
        if problems:
            self.out("Its rule cannot be used:\n" + "\n".join(f"  - {problem}" for problem in problems))
            return
        self.out(f"{code.rstrip()}\n\nWhy: {reason}\n"
                 f"It gives the recorded answer for all {len(shown)} cases it was shown. Of the {len(held)} held back, "
                 f"it answers {answered} correctly and leaves {len(held) - answered} to the model.")
        if not self.confirm(f"Use this rule for step {step} of {name}?", default=False):
            self.out("Not installed.")
            return
        path = rules.install(name, step, program.instructions[step - 1], register, code, self.author, len(listed))
        self.out(f"Installed. Step {step} of {name} now asks the rule first, and the model only when the rule "
                 f"has no answer.\nDelete {path} to remove it.")

    def list_rules(self):
        current = rules.installed()
        if not current:
            self.out("No rules are installed.")
        for (name, step), record in sorted(current.items()):
            self.out(f"{name} step {step}: from {record['cases']} recorded cases, written by "
                     f"{record['written_by']} on {record['date']}")

    def stats(self):
        log = state.read("dispatches")
        asked = [entry["seconds"] or 0 for entry in log if entry["how"] == "model"]
        recalled = [entry["seconds"] or 0 for entry in log if entry["how"] == "memory"]
        self.out(f"Plain-language lines: {len(log)}\n"
                 f"Answered by the model: {len(asked)}, taking {sum(asked):.1f} s\n"
                 f"Answered from memory: {len(recalled)}, saving about {sum(recalled):.1f} s")
        cycles = state.read("cycles")
        if cycles:
            decoded = [cycle["seconds"] or 0 for cycle in cycles if cycle.get("how") not in ("memory", "rule", "pinned")]
            recalled = [cycle.get("saved") or 0 for cycle in cycles if cycle.get("how") == "memory"]
            ruled = sum(cycle.get("how") in ("rule", "pinned") for cycle in cycles)
            self.out(f"Program steps: {len(cycles)}\n"
                     f"Decoded by the model: {len(decoded)}, taking {sum(decoded):.1f} s\n"
                     f"Answered from memory: {len(recalled)}, saving about {sum(recalled):.1f} s\n"
                     f"Answered by a rule or a pin: {ruled}")

    def queue(self, record, decided_by):
        state.append("needs", {**record, "decided_by": decided_by})
        self.out(f"Nothing here does that yet. Queued as a new need ({len(state.read('needs'))} waiting).")

    def run(self, command, args):
        try:
            if command.program:
                text = machine.run(command.name, command.program, self.table, self.decoder, args)
            else:
                text = command.run(**args)
        except ModelUnavailable as error:
            self.out(f"{command.name} is a program, and the model that decodes it is unavailable: {error}.")
            return
        except (plugins.CommandError, OSError) as error:  # OSError: permission denied, missing file
            self.out(f"{command.name}: {error}")
            return
        if text:
            self.out(text)

    def confirm(self, question, default):
        try:
            answer = self.ask(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        except EOFError:  # nobody is there to agree
            return False
        return default if not answer else answer.startswith("y")

    def numbered_need(self, verb, words):
        """The queue and the need a builtin was pointed at, or None after explaining the usage."""
        waiting = state.read("needs")
        if len(words) != 1 or not words[0].isdigit() or not 1 <= int(words[0]) <= len(waiting):
            self.out(f"Usage: {verb} NUMBER, where NUMBER is one of those listed by needs.")
            return waiting, None
        return waiting, waiting[int(words[0]) - 1]

    def forget(self, words):
        waiting, need = self.numbered_need("forget", words)
        if need:
            state.replace("needs", [other for other in waiting if other is not need])
            self.out(f"Forgotten: {need['line']}")

    def teach(self, words):
        """Ask the author model for a command that handles a queued need, and install it if the
        user agrees and the result passes the acceptance check."""
        waiting, need = self.numbered_need("teach", words)
        if not need:
            return
        if not self.author or not self.plugin_dir:
            self.out("No model is set up to write commands. Give the author role a provider in los.toml.")
            return
        self.out(f"Asking {self.author.model} to write a command for: {need['line']}")
        try:
            proposal = teach.ask(self.author, need["line"], self.table, state.ROOT / "plugins" / "fs")
        except (ModelUnavailable, RuntimeError) as error:
            self.out(f"No proposal came back: {error}")
            return
        if proposal.declined:
            self.out(f"It declined: {proposal.reason}\nThe need stays queued; forget {words[0]} removes it.")
            return
        problems = teach.check(proposal, self.table)
        if problems:
            self.out(f"Its proposal for {proposal.name} cannot be installed:\n" +
                     "\n".join(f"  - {problem}" for problem in problems) + "\nThe need stays queued.")
            return
        self.out(teach.render(proposal))
        if not self.confirm(f"Install {proposal.name}?", default=False):
            self.out("Not installed. The need stays queued.")
            return

        folder = teach.install(proposal, self.plugin_dir, need["line"], self.author)
        failures = self.acceptance(folder, proposal, need["line"])
        if failures:
            shutil.rmtree(folder)
            self.out(f"{proposal.name} was removed again, because it failed the acceptance check:\n" +
                     "\n".join(f"  - {failure}" for failure in failures) + "\nThe need stays queued.")
            return
        earlier_version = self.table_version
        self.table = plugins.load(self.plugin_dir)
        self.table_version = plugins.version(self.table)
        # The check just replayed every settled line against the new table, so they stay settled.
        memory.carry(earlier_version, self.table_version)
        state.replace("needs", [other for other in waiting if other is not need])
        state.append("authored", {"date": datetime.date.today().isoformat(), "line": need["line"],
                                  "command": proposal.name, "provider": self.author.provider,
                                  "model": self.author.model, "table": self.table_version})
        self.out(f"Installed {proposal.name} in {folder}. Delete that folder to remove it.")

    def acceptance(self, folder, proposal, line):
        """What must hold before a new command stays: it loads, the lines the user has already
        accepted still reach the same commands, and the need's own line reaches the new one."""
        try:
            table = plugins.load(self.plugin_dir)
        except Exception as error:  # anything the new module does wrong while loading
            return [f"it does not load: {error}"]
        accepted = {earlier: label["command"] for earlier, label in memory.recall(self.table_version).items()}
        self.out(f"Checking it against {len(accepted)} line(s) you accepted before, and the need itself.")
        failures = []
        try:
            for earlier, expected in accepted.items():
                now = dispatch.ask(self.model, table, earlier).command
                if now != expected:
                    failures.append(f"\"{earlier}\" used to reach {expected} and would now reach {now or 'nothing'}")
            now = dispatch.ask(self.model, table, line).command
            if now != proposal.name:
                failures.append(f"\"{line}\" reaches {now or 'nothing'}, not {proposal.name}")
        except (ModelUnavailable, RuntimeError) as error:
            failures.append(f"the check needs the model that reads plain language: {error}")
        return failures

    def help(self, names):
        if not names:
            width = max(map(len, self.table))
            self.out("\n".join(f"{name:{width}}  {self.table[name].description}" for name in sorted(self.table)))
            self.out("\nhelp COMMAND shows a command's parameters. needs lists what is queued;\n"
                     "teach NUMBER asks for a command to be written for one, forget NUMBER drops it.\n"
                     "Anything else is read as plain language and matched to a command. A line you\n"
                     "accepted before is remembered; wrong takes the latest such choice back, and\n"
                     "stats shows how often memory answered. trace shows the steps of the latest\n"
                     "program run, rule PROGRAM STEP turns a step's recorded answers into a rule,\n"
                     "and rules lists those in use. exit leaves.")
            return
        for name in names:
            if name in BUILTINS:
                self.out(BUILTINS[name])
                continue
            if name not in self.table:
                self.out(f"There is no command named {name}.")
                continue
            command = self.table[name]
            width = max(map(len, command.params), default=0)
            self.out(f"{command.description}\n{usage(command)}")
            self.out("\n".join(f"  --{key.replace('_', '-'):{width}}  {hint}" for key, hint in command.params.items()))

    def needs(self):
        waiting = state.read("needs")
        if not waiting:
            self.out("No needs are waiting.")
        for number, need in enumerate(waiting, 1):
            self.out(f"{number}. {need['line']}  ({need['date']})")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="lo-s", description="The lo-s shell.")
    parser.add_argument("-c", dest="line", metavar="LINE", help="run one line and exit")
    opts = parser.parse_args(argv)

    config = tomllib.loads((state.ROOT / "los.toml").read_text())
    plugin_dir = pathlib.Path(os.environ.get("LOS_PLUGINS", state.ROOT / "plugins"))

    def role(name):
        return provider(config["providers"][config["roles"][name]]) if name in config["roles"] else None

    shell = Shell(plugins.load(plugin_dir), role("dispatch"), author=role("author"), plugin_dir=plugin_dir,
                  decoder=role("decode"))
    if opts.line is not None:
        shell.handle(opts.line)
        return

    import readline  # noqa: F401  gives input() line editing and history
    while True:
        try:
            if not shell.handle(input("lo-s> ")):
                break
        except EOFError:
            print()
            break
        except KeyboardInterrupt:
            print("\nInterrupted.")
