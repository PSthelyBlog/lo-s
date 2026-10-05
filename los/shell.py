"""The shell: read a line, run it as a command, or find out which command was meant.

A line goes through three steps. A structured command runs directly, with no model involved.
Anything else goes to the dispatch model, whose choice is shown in typed form and run only if
the user agrees. If the model finds no fitting command, the line is queued as a new need, and
`teach` asks the author model to write a command for it.
"""
import argparse
import datetime
import os
import pathlib
import shutil
import tomllib

from . import dispatch, plugins, state, teach
from .models import ModelUnavailable, provider
from .parse import UsageError, parse, render, usage


class Shell:
    def __init__(self, table, model, ask=input, out=print, author=None, plugin_dir=None):
        self.table, self.model, self.ask, self.out = table, model, ask, out
        self.author, self.plugin_dir = author, plugin_dir
        self.table_version = plugins.version(table)

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
        """Plain language: ask the model, show its choice as a typed command, run it if agreed."""
        try:
            choice = dispatch.ask(self.model, self.table, line)
        except ModelUnavailable as error:
            self.out(f"That is not a command, and the model that reads plain language is unavailable: {error}.\n"
                     "Structured commands still work; type help to list them.")
            return
        except RuntimeError as error:
            self.out(f"The model's answer could not be used: {error}")
            return
        record = {"date": datetime.date.today().isoformat(), "line": line, "table": self.table_version,
                  "provider": self.model.provider, "model": self.model.model,
                  "seconds": choice.meta.get("seconds")}
        if choice.command is None:
            self.queue(record, "model")
            return
        command = self.table[choice.command]
        self.out("→ " + render(command.name, choice.args))
        # Reading is safe to accept with Enter; anything that changes state needs an explicit yes.
        accepted = self.confirm("Run it?", default=command.effect == "read")
        # An accepted choice is a label: this line means this command, for this version of the
        # table. A declined one is not: the choice may be right and simply unwanted just now.
        state.append("labels", {**record, "command": command.name, "args": choice.args,
                                "verdict": "accepted" if accepted else "declined"})
        if accepted:
            self.run(command, choice.args)
        elif self.confirm("Queue it as a new need instead?", default=False):
            self.queue(record, "user")
        else:
            self.out("Not run.")

    def queue(self, record, decided_by):
        state.append("needs", {**record, "decided_by": decided_by})
        self.out(f"Nothing here does that yet. Queued as a new need ({len(state.read('needs'))} waiting).")

    def run(self, command, args):
        try:
            text = command.run(**args)
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
        self.table = plugins.load(self.plugin_dir)
        self.table_version = plugins.version(self.table)
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
        accepted = {label["line"]: label["command"] for label in state.read("labels")
                    if label["verdict"] == "accepted" and label["command"] in table}
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
                     "exit leaves. Anything else is read as plain language and matched to a command.")
            return
        for name in names:
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

    shell = Shell(plugins.load(plugin_dir), role("dispatch"), author=role("author"), plugin_dir=plugin_dir)
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
