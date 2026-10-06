"""Fixing how a model's weights are split between RAM and the GPU.

Left to itself, llama.cpp decides at every start which weights go on the GPU, from the GPU
memory free at that moment. Another split gives other answers (see experiments/restart): one
instruction, with one input, decoded differently after a restart because another program was
using the GPU. So the split is chosen once, written down, and repeated at every later start. If
it no longer fits, the server refuses to start instead of answering differently.
"""
import datetime
import os
import pathlib
import re
import subprocess
import time

from . import state, tune

KEPT = re.compile(r"tensor (\S+) \(.*?\) buffer type overridden to (CPU|\w+_Host)\b")
OFFLOADED = re.compile(r"offloaded (\d+)/\d+ layers to GPU")
BUFFER = re.compile(r"(\w+) model buffer size = +([\d.]+) MiB")


def read(log):
    """What a server log made with `-lv 5` says about the load that was used: how many layers
    went to the GPU, which of their tensors were kept in RAM anyway, and the MiB of weights
    that ended up on the GPU."""
    final = log.rsplit("loading model tensors", 1)[-1]      # earlier loads are llama.cpp trying sizes
    offloaded = OFFLOADED.search(final)
    if not offloaded:
        raise ValueError("the server log does not say how many layers went to the GPU")
    kept = list(dict.fromkeys(name for name, _ in KEPT.findall(final)))
    on_gpu = sum(float(size) for device, size in BUFFER.findall(final)
                 if not device.startswith("CPU") and not device.endswith("_Host"))
    return int(offloaded.group(1)), kept, round(on_gpu)


def patterns(tensors):
    """Regular expressions that match exactly these tensor names. Tensors of the same name in
    different blocks share one pattern."""
    blocks, others = {}, []
    for name in tensors:
        block = re.fullmatch(r"blk\.(\d+)\.(.+)", name)
        if block:
            blocks.setdefault(block.group(2), []).append(block.group(1))
        else:
            others.append(name)
    return ([rf"^blk\.({'|'.join(numbers)})\.{re.escape(rest)}$" for rest, numbers in blocks.items()] +
            [f"^{re.escape(name)}$" for name in others])


def arguments(gpu_layers, tensors):
    """The llama-server arguments that load a model with exactly this split."""
    args = ["--fit", "off", "--gpu-layers", str(gpu_layers)]
    if tensors:
        args += ["--override-tensor", ",".join(pattern + "=CPU" for pattern in patterns(tensors))]
    return args


def split_file(model, folder=None):
    return pathlib.Path(folder or state.ROOT / "runtime" / "tuned") / f"{model}.split"


def write(model, gpu_layers, tensors, on_gpu, folder=None):
    """Record the split where serve.sh looks for it."""
    path = split_file(model, folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Chosen by llama.cpp on {datetime.date.today().isoformat()}: {gpu_layers} layers on the GPU "
                    f"({on_gpu} MiB of weights), {len(tensors)} of their tensors kept in RAM.\n"
                    "# scripts/serve.sh repeats this split at every start. Delete this file to choose again.\n" +
                    " ".join(arguments(gpu_layers, tensors)) + "\n")
    return path


def choose(model, port=8089, log=None):
    """Start the server once with nothing fixed, and return the split llama.cpp chose for it."""
    log = pathlib.Path(log or split_file(model).with_suffix(".split.log"))
    log.parent.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "LOS_NO_SPLIT": "1", "PORT": str(port)}
    with open(log, "w") as out:
        server = subprocess.Popen([state.ROOT / "scripts" / "serve.sh", model, "-lv", "5"],
                                  stdout=out, stderr=out, env=environment)
        try:
            while not tune.healthy(port):
                if server.poll() is not None:
                    raise RuntimeError(f"the server did not start; see {log}")
                time.sleep(1)
        finally:
            server.terminate()
            server.wait()
    return read(log.read_text(errors="replace"))
