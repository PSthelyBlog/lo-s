"""What the user's answers have settled: which plain-language lines mean which command.

A label says that a line means a command for one version of the command table. `recall` gives
the lines settled for a version, so the shell can answer them without asking the model. When the
table changes, the lines that were checked against the new table are carried over with `carry`;
anything else has to be settled again.
"""
from . import state


def recall(table_version):
    """Lines settled for this version of the table, as line -> the label that settled it."""
    settled = {}
    for label in state.read("labels"):
        if label.get("table") != table_version:
            continue
        if label["verdict"] == "accepted":
            settled[label["line"]] = label
        elif label["verdict"] == "wrong":
            settled.pop(label["line"], None)
    return settled


def carry(old_version, new_version):
    """Stamp the lines settled under the old table for the new one. Only call this once each of
    them has been checked against the new table."""
    for label in recall(old_version).values():
        state.append("labels", {**label, "table": new_version, "carried_from": old_version})
