"""Status of the machine, read from /proc and /sys."""
import glob
import os
import pathlib

from los.plugins import CommandError


def _read(path):
    return pathlib.Path(path).read_text().strip()


def _battery():
    return [f"Battery: {_read(supply + '/capacity')}%, {_read(supply + '/status').lower()}"
            for supply in sorted(glob.glob("/sys/class/power_supply/BAT*"))] or ["Battery: none found"]


def _cpu():
    one, five, fifteen = _read("/proc/loadavg").split()[:3]
    return [f"CPU: load {one} over 1 minute, {five} over 5, {fifteen} over 15, on {os.cpu_count()} cores"]


def _memory():
    kilobytes = {line.split(":")[0]: int(line.split()[1]) for line in _read("/proc/meminfo").splitlines()}
    return [f"Memory: {kilobytes['MemAvailable'] / 1048576:.1f} GB available of {kilobytes['MemTotal'] / 1048576:.1f} GB"]


def _temperature():
    readings = []
    for zone in glob.glob("/sys/class/thermal/thermal_zone*"):
        try:
            readings.append((int(_read(zone + "/temp")) / 1000, _read(zone + "/type")))
        except (OSError, ValueError):
            pass
    if not readings:
        return ["Temperature: no sensor found"]
    degrees, sensor = max(readings)
    return [f"Temperature: {degrees:.0f} °C at the hottest sensor ({sensor})"]


PARTS = {"battery": _battery, "cpu": _cpu, "memory": _memory, "temperature": _temperature}


def do_status(what=None):
    wanted = (what or "all").lower()
    if wanted != "all" and wanted not in PARTS:
        raise CommandError(f"--what is {', '.join(PARTS)} or all, not {what!r}")
    return "\n".join(line for name, part in PARTS.items() if wanted in ("all", name) for line in part())
