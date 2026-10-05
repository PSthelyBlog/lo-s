"""The fallback for a line that is not a structured command: ask a model which command was meant."""
import dataclasses

from .models import complete_valid

INSTRUCTIONS = """\
You are the dispatcher of a command-line operating system. Users normally type structured \
commands. The line you receive did not parse as one, so it is probably plain language. Decide \
which command from the table, if any, the user meant.

Pick a command only when running it would do what the user asked for. A wrong command is worse \
than no command: when nothing in the table does the job, answer "none", and the request is \
queued so that a new command can be written for it. When several commands could serve, pick \
the most specific one.

For args, use only the parameters listed for the chosen command, take the values from the \
user's line, and leave out parameters the line says nothing about.

Reply with JSON of the form {"call": {"command": "<name>", "args": {"<parameter>": "<value>"}}}, \
or {"call": {"command": "none"}} when nothing fits.

Commands, one per line as: name | what it does | parameters
"""


@dataclasses.dataclass
class Choice:
    command: str | None     # None when the model says nothing in the table fits
    args: dict
    meta: dict              # timing and token counts from the provider


def table_text(table):
    """The command table, one row per command, sorted by name so position carries no hint."""
    rows = []
    for command in sorted(table.values(), key=lambda c: c.name):
        params = ", ".join(f"{name} ({hint})" if hint else name for name, hint in command.params.items())
        rows.append(f"{command.name} | {command.description} | {params}".rstrip(" |"))
    return "\n".join(rows)


def system_prompt(table):
    return INSTRUCTIONS + table_text(table)


def schema(table):
    """One allowed form per command, so the model can only name parameters that command has."""
    def form(properties):
        return {"type": "object", "additionalProperties": False, "required": list(properties),
                "properties": properties}

    forms = [form({"command": {"const": command.name},
                   "args": {"type": "object", "additionalProperties": False,
                            "properties": {name: {"type": "string"} for name in command.params}}})
             for command in sorted(table.values(), key=lambda c: c.name)]
    forms.append(form({"command": {"const": "none"}}))
    return form({"call": {"anyOf": forms}})


def ask(model, table, line):
    """Ask the model which command the line means."""
    output, meta = complete_valid(model, system_prompt(table), line, schema(table))
    call = output["call"]
    if call["command"] == "none":
        return Choice(None, {}, meta)
    return Choice(call["command"], call["args"], meta)
