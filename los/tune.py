"""Searching for a faster way to start the model server.

A candidate is a set of extra llama-server arguments. It is tried by starting the server with
them, replaying recorded lines and timing the answers. It is accepted only if every line gets
the answer on record and enough GPU memory stays free. An accepted candidate replaces the best
so far only if it is faster by a clear margin, so that noise between runs does not pass for an
improvement.
"""
import dataclasses
import os
import pathlib
import statistics
import subprocess
import time
import urllib.request

from . import dispatch, state
from .models import OpenAICompat

MARGIN = 0.05       # how much faster than the best so far a candidate must be to replace it


@dataclasses.dataclass
class Result:
    args: list                          # the extra server arguments tried
    seconds: float | None = None        # median seconds per line
    tokens_per_second: float | None = None
    gpu_free: int | None = None         # MiB left on the GPU with the model loaded
    changed: int = 0                    # lines whose answer differs from the record
    problem: str = ""                   # why it was not accepted; empty if it was

    @property
    def accepted(self):
        return not self.problem


def search(run, stages, margin=MARGIN):
    """Try the default, then each stage's alternatives on top of the best found so far.

    `run(args)` measures one candidate and returns a Result. Returns (best, every result), with
    best None when the default itself is not accepted, since nothing can then be compared to it.
    """
    best = run([])
    results = [best]
    if not best.accepted:
        return None, results
    for stage in stages:
        base = best.args
        for extra in stage:
            result = run(base + extra)
            results.append(result)
            if result.accepted and result.seconds < best.seconds * (1 - margin):
                best = result
    return best, results


def stages():
    """What to vary, in order: how the weights are loaded, how many threads compute the expert
    layers kept in RAM, and how the layers are split between RAM and GPU."""
    logical, physical = os.cpu_count() or 1, physical_cores()
    threads = [["--threads", str(count)] for count in sorted({(physical + logical) // 2, logical}) if count > physical]
    return [[["--load-mode", "none"]], threads, [["--cpu-moe"], ["--fit-target", "512"]]]


def physical_cores():
    try:
        cores = {tuple(line.split(":")[1].strip() for line in block.splitlines()
                       if line.startswith(("physical id", "core id")))
                 for block in pathlib.Path("/proc/cpuinfo").read_text().strip().split("\n\n")} - {()}
        return len(cores) or os.cpu_count() or 1
    except OSError:
        return os.cpu_count() or 1


def healthy(port):
    try:
        return urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2).status == 200
    except OSError:
        return False


def gpu_free():
    """MiB free on the first GPU, or None when that cannot be read."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True).stdout
        return int(out.split()[0])
    except (OSError, ValueError, IndexError):
        return None


def measure(model, args, table, record, headroom, port=8080, log=None):
    """Start the server with these arguments, replay the record against it, and stop it.

    `record` is a list of (line, command, args) with the answers on file for this table.
    """
    result = Result(list(args))
    environment = {**os.environ, "LOS_NO_TUNED": "1", "LOS_NO_SPLIT": "1", "PORT": str(port)}
    server = subprocess.Popen([state.ROOT / "scripts" / "serve.sh", model, *args],
                              stdout=log, stderr=log, env=environment)
    try:
        while not healthy(port):
            if server.poll() is not None:
                result.problem = "the server did not start"
                return result
            time.sleep(1)
        result.gpu_free = gpu_free()
        student = OpenAICompat(f"http://127.0.0.1:{port}/v1", model)
        seconds, speeds = [], []
        for line, command, expected in record:
            choice = dispatch.ask(student, table, line)
            seconds.append(choice.meta["seconds"])
            speeds.append(choice.meta.get("output_tokens_per_second") or 0)
            result.changed += (choice.command, choice.args) != (command, expected)
        result.seconds, result.tokens_per_second = statistics.median(seconds), statistics.median(speeds)
    finally:
        server.terminate()
        server.wait()
    if result.changed:
        result.problem = f"{result.changed} of {len(record)} answers differ from the record"
    elif result.gpu_free is not None and result.gpu_free < headroom:
        result.problem = f"only {result.gpu_free} MiB of GPU memory left free"
    return result


def tuned_file(model, folder=None):
    return pathlib.Path(folder or state.ROOT / "runtime" / "tuned") / f"{model}.args"


def apply(model, best, folder=None):
    """Make serve.sh use the winning arguments for this model, or the defaults if they won.

    When that changes what serve.sh uses, the split it has been repeating is dropped, because it
    was chosen under the old arguments. The next start chooses one again.
    """
    path = tuned_file(model, folder)
    before = path.read_text() if path.exists() else ""
    if best.args:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(" ".join(best.args) + "\n")
    elif path.exists():
        path.unlink()
    if before != (path.read_text() if path.exists() else ""):
        path.with_suffix(".split").unlink(missing_ok=True)
    return path


def row(result, best=None):
    """One line of the report table."""
    def number(value, form):
        return format(value, form) if value is not None else ""

    verdict = result.problem or ("accepted, fastest" if result is best else "accepted")
    return (f"| {' '.join(result.args) or '(defaults)'} | {number(result.seconds, '.2f')} | "
            f"{number(result.tokens_per_second, '.0f')} | {number(result.gpu_free, 'd')} | {verdict} |")


HEADER = ("| Extra server arguments | Seconds per line | Tokens per second | GPU MiB free | Verdict |\n"
          "|---|---|---|---|---|")
