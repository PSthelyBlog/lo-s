"""Working out what to send for something the user wants.

The user says what they need in their own words. The author model is told how lo-s works, which
commands exist and which models this machine is set up with, and answers with what to send: a
command that exists, a need for a new command, a question, or the news that lo-s cannot do it.
It returns text only. The shell shows the answer and asks before anything is sent.
"""
import dataclasses

from .dispatch import table_text
from .models import complete_valid

BRIEF = """\
You know how lo-s works, so that its user does not have to. lo-s is a command-line operating \
system. You receive what the user wants, in their own words. Answer with what should be sent to \
lo-s for it. The user sees your answer and agrees before anything is sent.

How lo-s handles a line.
- A command is named plugin.verb and takes named parameters, all optional strings. Typed as \
plugin.verb --param value, it runs directly.
- Any other line is plain language. A small local model, which users call the student, reads the \
command table and either picks a command and fills in its parameters, or says that nothing fits. \
The user sees its choice in typed form and agrees before it runs.
- A line nothing fits is queued as a need. A stronger model, which users call the teacher, is \
then asked to write a new command for it. The teacher is you, in another call that will not have \
seen this one. The user reads the code and agrees before it is installed.

What a command can be. One Python function in a module of its own. It takes its parameters as \
strings and returns the text to show. It may use only the standard library, with which it can \
read and write files, use the network, run other programs and keep records between runs. It runs \
once for a line and ends: it cannot hold a session open, and it cannot ask the user anything \
while it runs. One need gives one command.

Choose one answer.

"run": a command in the table does what the user wants. Give its name in command and the \
parameters to send in args, using only the parameters the table lists for it and values taken \
from the user's words. Leave out what the user said nothing about. Do not stretch a command to \
something it does not do: a wrong command is worse than none.

"need": no command does it, and one could. Give two things.
- example: one line the user would type to use the new command once it exists, in plain words, \
such as: how many lines are in notes.md. After the command is installed the student is asked \
this very line and has to choose the new command for it, or the command is removed again. So \
make it one short, ordinary use, and put nothing in it but what a user would type.
- notes: what the author has to know and the example does not say. What a user would vary from \
one use to the next. What on this machine the command has to reach, with the exact address or \
program, taken from what you are told below. Which lines the command is meant for and which it \
is not, so that it does not draw in lines meant for other commands or lines that should be \
queued. Write no code and name no parameters; the author does that. Keep the notes to one short \
paragraph of plain sentences without lists, because the user reads them before agreeing, and \
leave out anything the user did not ask for.

"ask": the user's words leave open something that changes what you would send. Put one question \
in question.

"cannot": lo-s cannot do it. It needs a service, an account or hardware that nothing below \
shows, or it is not something a command can do.

Always give reason: one or two plain sentences for the user, saying why this is the answer.
"""

TEXT = {"type": "string"}
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answer", "reason"],
    "properties": {
        "answer": {"type": "string", "enum": ["run", "need", "ask", "cannot"]},
        "reason": TEXT,
        "command": TEXT,                                    # run
        "args": {"type": "array", "items": {                # run
            "type": "object", "additionalProperties": False, "required": ["name", "value"],
            "properties": {"name": TEXT, "value": TEXT}}},
        "example": TEXT,                                    # need
        "notes": TEXT,                                      # need
        "question": TEXT,                                   # ask
    },
}


@dataclasses.dataclass
class Advice:
    answer: str                 # run, need, ask or cannot
    reason: str
    command: str = ""           # run: the command to send
    args: dict = dataclasses.field(default_factory=dict)
    example: str = ""           # need: a line that would use the new command
    notes: str = ""             # need: what the author must know beyond that line
    question: str = ""          # ask


def setup(roles):
    """Which model fills which role, and how a program on this machine reaches it."""
    filled = {}
    for role, model in roles.items():
        if model:
            filled.setdefault(model.describe(), []).append(role)
    return "\n".join(f"{' and '.join(names)}: {reached}" for reached, names in filled.items())


def ask(author, wish, table, roles):
    """Ask the author model what to send for what the user wants."""
    system = (BRIEF + "\nThe models this machine is set up with, by the role they fill. dispatch reads "
              "plain-language lines and decode runs commands written as instructions: that is the student. "
              "author writes commands: that is the teacher.\n" + setup(roles) +
              "\n\nCommands, one per line as: name | what it does | parameters\n" + table_text(table))
    output, _ = complete_valid(author, system, f"What the user wants: {wish}", SCHEMA)
    answer, reason = output["answer"], _line(output["reason"])
    if answer == "run":
        command = table.get(output.get("command"))
        if not command:
            raise RuntimeError(f"it named a command that does not exist: {output.get('command')!r}")
        args = {arg["name"].replace("-", "_"): arg["value"] for arg in output.get("args", [])}
        unknown = sorted(set(args) - set(command.params))
        if unknown:
            raise RuntimeError(f"it gave {command.name} a parameter it does not have: {', '.join(unknown)}")
        return Advice(answer, reason, command=command.name, args=args)
    if answer == "need":
        example, notes = _line(output.get("example", "")), _line(output.get("notes", ""))
        if not example or not notes:
            raise RuntimeError("it asked for a new command but left out the example line or the notes")
        return Advice(answer, reason, example=example, notes=notes)
    if answer == "ask":
        if not _line(output.get("question", "")):
            raise RuntimeError("it wanted to ask something but gave no question")
        return Advice(answer, reason, question=_line(output["question"]))
    return Advice(answer, reason)


def _line(text):
    """The text on one line. A need is typed, listed and stored as a line."""
    return " ".join(text.split())
