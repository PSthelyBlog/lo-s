#!/usr/bin/env python3
"""Hold some GPU memory until stopped, as another program using the GPU would.

Asks the NVIDIA driver for the memory directly, so nothing needs installing. Prints one line
once the memory is held.

Usage: hold-gpu.py MIB
"""
import ctypes
import signal
import sys


def main():
    mib = int(sys.argv[1])
    cuda = ctypes.CDLL("libcuda.so.1")
    device, context, memory = ctypes.c_int(), ctypes.c_void_p(), ctypes.c_uint64()
    steps = [("cuInit", lambda: cuda.cuInit(0)),
             ("cuDeviceGet", lambda: cuda.cuDeviceGet(ctypes.byref(device), 0)),
             ("cuCtxCreate", lambda: cuda.cuCtxCreate_v2(ctypes.byref(context), 0, device)),
             ("cuMemAlloc", lambda: cuda.cuMemAlloc_v2(ctypes.byref(memory), ctypes.c_size_t(mib << 20)))]
    for name, step in steps:
        error = step()
        if error:
            sys.exit(f"{name} failed with CUDA error {error}")
    print(f"holding {mib} MiB", flush=True)
    signal.pause()


if __name__ == "__main__":
    main()
