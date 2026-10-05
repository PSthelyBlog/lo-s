"""The shell: read a line, run it as a command, or find out which command was meant.

A line goes through three steps. A structured command runs directly, with no model involved.
Anything else goes to the dispatch model, whose choice is shown in typed form and run only if
the user agrees. If the model finds no fitting command, the line is queued as a new need.
"""
import argparse
import datetime
import tomllib

from . import dispatch, plugins, state
from .models import ModelUnavailable, provider
from .parse import UsageError, parse, render, usage


class Shell:
    def __init__(self, table, model, ask=input, out=print):
        self.table, self.model, self.ask, self.out = table, model, ask, out
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
        # The user's answer is a label for this line against this version of the command table.
        state.append("labels", {**record, "command": command.name, "args": choice.args,
                                "verdict": "accepted" if accepted else "rejected"})
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

    def help(self, names):
        if not names:
            width = max(map(len, self.table))
            self.out("\n".join(f"{name:{width}}  {self.table[name].description}" for name in sorted(self.table)))
            self.out("\nhelp COMMAND shows a command's parameters. needs lists what is queued. exit leaves.\n"
                     "Anything else is read as plain language and matched to a command.")
            return
        for name in names:
            if name not in self.table:
                self.out(f"There is no command named {name}.")
                continue
            command = self.table[name]
            self.out(f"{command.description}\n{usage(command)}")
            self.out("\n".join(f"  --{key.replace('_', '-')}  {hint}" for key, hint in command.params.items()))

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
    table = plugins.load(state.ROOT / "plugins")
    shell = Shell(table, provider(config["providers"][config["roles"]["dispatch"]]))
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
