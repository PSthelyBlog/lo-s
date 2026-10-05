"""Short text notes, kept in the state directory."""
import datetime

from los import state
from los.plugins import CommandError


def do_add(text=None, tag=None):
    if not text:
        raise CommandError("needs --text")
    state.append("notes", {"date": datetime.date.today().isoformat(), "text": text, "tag": tag})
    return "Noted."


def do_list(tag=None):
    notes = [note for note in state.read("notes") if not tag or note["tag"] == tag]
    return "\n".join(f"{note['date']}  {note['text']}" + (f"  [{note['tag']}]" if note["tag"] else "")
                     for note in notes) or "No notes."
