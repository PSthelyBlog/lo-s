# lo-s

A command-line operating system on an emulated machine whose processor is a language model.
`SPEC.md` describes the design and marks what is decided, proposed and open.

## Try the shell

Needs Linux, Python 3.11 or later and, for the local model, an NVIDIA GPU.

```
scripts/setup-runtime.sh                              # once: llama.cpp build and models, about 20 GB
scripts/serve.sh gemma-4-26B-A4B-it-qat-q4_0.gguf     # in another terminal: the local model
./lo-s
```

Type a structured command such as `fs.usage --path ~ --depth 2`, or plain language such as
"what's eating all the space in my home directory". The shell shows which command it understood
and asks before running it. `help` lists the commands. Structured commands work without the model
running.

## Layout

- `los/`: the shell, the dispatcher, the plugin loader and the model providers
- `plugins/`: the starter commands
- `los.toml`: which provider fills which role
- `experiments/dispatch/`: the dispatch experiment and its results
- `scripts/`: runtime setup and measurements
- `tests/`: run with `python3 -m unittest discover -s tests -t .`
