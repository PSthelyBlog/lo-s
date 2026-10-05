"""File and directory commands."""
import fnmatch
import os
import pathlib
import re
import shutil
import time

from los.plugins import CommandError

UNITS = {"": 1, "b": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
PERIODS = {"minute": 60, "hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}
LIMIT = 200     # files listed by find before the rest is counted instead
SHOWN = 30      # directories listed by usage


def _path(text):
    return pathlib.Path(os.path.expanduser(text or "."))


def _directory(text):
    path = _path(text)
    if not path.is_dir():
        raise CommandError(f"{path} is not a directory")
    return path


def _size(text):
    """Bytes, from text such as 100M, 2G or 1.5 GB."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-z]*)\s*", text.lower())
    if not match or match[2][:1] not in UNITS:
        raise CommandError(f"a size looks like 100M or 2G, not {text!r}")
    return int(float(match[1]) * UNITS[match[2][:1]])


def _age(text):
    """Seconds, from text such as 30 days, 2 weeks or 1 year."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([a-z]+?)s?\s*", text.lower())
    periods = [period for period in PERIODS if match and period.startswith(match[2])]
    if len(periods) != 1:
        raise CommandError(f"an age looks like 30 days or 1 year, not {text!r}")
    return float(match[1]) * PERIODS[periods[0]]


def _human(size):
    for unit in "BKMGT":
        if size < 1024 or unit == "T":
            return f"{size:.0f}B" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024


def do_list(path=None, sort_by=None):
    directory = _directory(path)
    orders = {"name": lambda e: e[0].lower(), "size": lambda e: -e[1], "time": lambda e: -e[2]}
    if (sort_by or "name") not in orders:
        raise CommandError(f"--sort-by is name, size or time, not {sort_by!r}")
    entries = []
    for entry in os.scandir(directory):
        info = entry.stat(follow_symlinks=False)
        entries.append((entry.name + ("/" if entry.is_dir(follow_symlinks=False) else ""), info.st_size, info.st_mtime))
    entries.sort(key=orders[sort_by or "name"])
    return "\n".join(f"{_human(size):>8}  {time.strftime('%Y-%m-%d %H:%M', time.localtime(modified))}  {name}"
                     for name, size, modified in entries) or f"{directory} is empty."


def do_find(path=None, name=None, larger_than=None, older_than=None):
    directory = _directory(path)
    smallest = _size(larger_than) if larger_than else None
    newest = time.time() - _age(older_than) if older_than else None
    # A plain word matches anywhere in the file name; a pattern must match all of it.
    pattern = name and (name.lower() if set(name) & set("*?[") else f"*{name.lower()}*")
    found = []
    for folder, _, files in os.walk(directory):
        for file in files:
            if pattern and not fnmatch.fnmatch(file.lower(), pattern):
                continue
            full = os.path.join(folder, file)
            try:
                info = os.lstat(full)
            except OSError:
                continue
            if smallest is not None and info.st_size <= smallest:
                continue
            if newest is not None and info.st_mtime >= newest:
                continue
            found.append((info.st_size, full))
    if not found:
        return "No files match."
    found.sort(key=lambda item: (-item[0], item[1]))    # largest first
    lines = [f"{_human(size):>8}  {full}" for size, full in found[:LIMIT]]
    if len(found) > LIMIT:
        lines.append(f"... and {len(found) - LIMIT} more")
    return "\n".join(lines)


def do_usage(path=None, depth=None):
    directory = _directory(path)
    try:
        levels = int(depth) if depth else 1
    except ValueError:
        raise CommandError(f"--depth is a whole number, not {depth!r}")
    totals = {}     # directory -> bytes on disk, everything beneath it included
    for folder, folders, files in os.walk(directory, topdown=False):
        size = sum(totals.get(os.path.join(folder, sub), 0) for sub in folders)
        for file in files:
            try:
                size += os.lstat(os.path.join(folder, file)).st_blocks * 512
            except OSError:
                pass
        totals[folder] = size
    root = str(directory)
    inside = sorted(((size, folder) for folder, size in totals.items()
                     if folder != root and len(pathlib.Path(folder).relative_to(directory).parts) <= levels),
                    key=lambda item: (-item[0], item[1]))
    lines = [f"{_human(totals[root]):>8}  {root}  (total)"]
    lines += [f"{_human(size):>8}  {folder}" for size, folder in inside[:SHOWN]]
    if len(inside) > SHOWN:
        lines.append(f"... and {len(inside) - SHOWN} smaller directories")
    return "\n".join(lines)


def do_move(source=None, dest=None):
    if not source or not dest:
        raise CommandError("needs --source and --dest")
    origin, target = _path(source), _path(dest)
    if not os.path.lexists(origin):
        raise CommandError(f"{origin} does not exist")
    if target.is_dir():
        target = target / origin.name
    if os.path.lexists(target):
        raise CommandError(f"{target} already exists; nothing was moved")
    shutil.move(origin, target)
    return f"Moved {origin} to {target}"
