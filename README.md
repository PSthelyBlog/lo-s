# lo-s

lo-s is an experiment in building a command-line operating system on an emulated machine whose
processor is a language model. You type commands, a small core dispatches them to plugins, and
the system is meant to grow a new plugin when you ask for something it cannot do yet.

**Status: early and experimental.** What exists today is the shell's first step, turning a typed
line into a command, and one experiment measuring how well a local model does that. The rest of
the design is in [SPEC.md](SPEC.md), where every item is marked decided, proposed or open.

## What works today

```
lo-s> fs.usage --path . --depth 1
   19.6G  .  (total)
   19.5G  ./runtime
    3.2M  ./experiments
lo-s> how much ram is free
→ sys.status --what memory
Run it? [Y/n]
Memory: 25.2 GB available of 30.8 GB
lo-s> rename report.txt to report-final.txt
→ fs.move --source report.txt --dest report-final.txt
Run it? [y/N] y
Moved report.txt to report-final.txt
lo-s> make a backup copy of config.yaml
Nothing here does that yet. Queued as a new need (1 waiting).
lo-s> teach 1
Asking claude-opus-5-5 to write a command for: make a backup copy of config.yaml
fs.copy  Copy a file or directory, or make a backup copy next to the original ...  (effect: write)
...
Install fs.copy? [y/N] y
Checking it against 2 line(s) you accepted before, and the need itself.
Installed fs.copy in plugins/fs.copy. Delete that folder to remove it.
lo-s> make a backup copy of config.yaml
→ fs.copy --source config.yaml
Run it? [y/N] y
Copied config.yaml to config.yaml.bak
```

The transcript is shortened; paths in the real output are absolute.

- **Structured commands** have the form `plugin.verb --param value` and run directly. No model is
  involved, so they work with nothing else installed.
- **Plain language** goes to a local model, which picks a command. The shell shows that command in
  typed form and asks before running it. Enter accepts a command that only reads; one that changes
  anything needs an explicit yes.
- **A line you accepted before is remembered.** It is answered from memory instead of by the
  model, in about a tenth of the time. A command that only reads then runs at once; one that
  changes anything still asks. `wrong` takes the latest such choice back, and `stats` shows how
  often memory answered. Memory holds for one version of the command table.
- **A command can be a program**: a short list of plain-language instructions that the machine
  runs one at a time. The local model turns each instruction into a single micro-op, and the
  machine applies it. `sys.health` is the first one, and `trace` shows the steps of the latest run.
  A step whose input the model has decoded before is answered from memory.
- **Anything no command fits** is queued as a need. `needs` lists them.
- **`teach NUMBER`** asks a stronger "teacher" model to write a command for a queued need. You see
  the whole proposal, code included, and nothing is installed unless you agree. The new command
  is then checked: the lines you accepted before must still reach the same commands, and the
  need's own line must reach the new one. If not, it is removed again.
- There are eight starter commands, in the `fs`, `note` and `sys` plugins. `help` lists them.

## What the experiment found

One hundred plain-language lines were mapped to commands by a local model and by Claude Opus 5.5
as the reference, with command tables of 10, 50 and 200 entries.

- Gemma 4 26B-A4B chose the same command as the reference on 91% to 95% of lines, and did not get
  worse as the table grew. An 8B model fell to 57% at 200 commands.
- Gemma's file is 14.4 GB, but about 90% of it is expert weights that can stay in system RAM. On an
  8 GB laptop GPU it produced 32 to 40 tokens per second.

This is a first measurement on one machine, with lines written by the same author as the
commands. It is not a benchmark. Method, numbers and every recorded answer are in
[experiments/dispatch](experiments/dispatch/README.md).

## Try it

You need Linux and Python 3.11 or later. Nothing beyond the standard library is used.

```
git clone https://github.com/PSthelyBlog/lo-s.git
cd lo-s
./lo-s
```

That is enough for structured commands. For plain language you also need the local model, which
was tested on an NVIDIA GPU with 8 GB of memory, a recent driver and 30 GB of system RAM:

```
scripts/setup-runtime.sh llama big                    # llama.cpp build and Gemma, about 15 GB, into runtime/
scripts/serve.sh gemma-4-26B-A4B-it-qat-q4_0.gguf     # leave running in another terminal
```

`scripts/setup-runtime.sh` with no arguments also fetches the smaller model used in the
experiment. Nothing is installed outside the `runtime/` folder; delete it to undo.

`scripts/tune-server.py MODEL.gguf` searches for faster server settings on your machine. It keeps
a setting only if recorded lines still get the same answers, and `serve.sh` then uses the winner.
On the test machine nothing beat the defaults.

## Safety

lo-s runs commands on your real machine.

- A command chosen by a model is always shown first and never runs without your agreement. Once
  you have agreed to a read-only command for a line, that line runs it again without asking.
- A command written by a model is shown in full before it is installed, with what its imports let
  it do. Its code is read for a few things it must not contain, but there is no sandbox: once you
  agree, it runs with your permissions. Read it first.
- `fs.move` is the only starter command that changes files, and it refuses to overwrite.
- The model server listens on this machine only, and only pages served from this machine may read
  its answers in a browser.
- Local models make mistakes. In the experiment, one turned "what's listening on port 5432" into a
  command that would have stopped the process. Read the command before you agree to it.

## Models and providers

Any provider can fill any role; `los.toml` says which does what. There are two kinds:

- `openai`: any server with the OpenAI chat API, such as llama.cpp's `llama-server`.
- `claude-cli`: runs your own installed `claude` program. lo-s never reads, stores or sends that
  login. You sign in with Anthropic yourself, and your use is subject to their terms.

The setup script downloads software and models that come with their own licences and are not part
of this repository: llama.cpp (MIT), NVIDIA's CUDA runtime libraries (NVIDIA's licence), Gemma 4
(Apache 2.0 according to its model card) and LFM2.5 (LFM Open License v1.0).

## Layout

- `los/`: the shell, the dispatcher, the plugin loader and the model providers
- `plugins/`: the starter commands
- `los.toml`: which provider fills which role
- `experiments/dispatch/`: the dispatch experiment and its results
- `experiments/machine/`: the decode stability experiment and its results
- `scripts/`: runtime setup and measurements
- `tests/`: run with `python3 -m unittest discover -s tests -t .`

## Licence

MIT. See [LICENCE](LICENCE).
