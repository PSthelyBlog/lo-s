# Dispatch experiment

**Question.** When a typed line is not a structured command, does the local model pick the same
command as the teacher, and does that hold as the command table grows?

## Method

- `commands.tsv` holds 200 commands. A table of size N is its first N rows, shown to the model
  sorted by name. The first 10 rows are the starting commands; later rows add near misses
  (`time.timer` beside `remind.add`, `power.battery` beside `sys.status`) and unrelated plugins.
- `lines.tsv` holds 100 plain-language lines, each with the command its author had in mind for the
  table of 200. Fifteen are meant to fit no command at any size.
- Every model gets the same system prompt, the same single line as the user message, and the same
  JSON schema. It answers with a command name or `none`, plus arguments.
- Each answer is recorded once in `results/TAG-SIZE.jsonl` with provider, model and date, and is
  never asked for again.
- `score.py` compares a student's records with the teacher's.

## Running it

```
scripts/setup-runtime.sh                                   # llama.cpp build and models, into runtime/
experiments/dispatch/dispatch.py claude-cli teacher-opus-5-5 --workers 4
experiments/dispatch/run-student.sh TAG MODEL.gguf [llama-server arguments]
experiments/dispatch/score.py teacher-opus-5-5 TAG
scripts/placement-sweep.py MODEL.gguf 30 22 auto           # speed against expert layers kept in RAM
experiments/dispatch/replay-shell.py TAG                   # the shell's own dispatcher, server running
scripts/tune-server.py MODEL.gguf                          # search server settings against the record
```

## Results, 2026-10-05

In short:

- The large student (Gemma 4 26B-A4B) picks the teacher's command on 91% to 95% of lines, and that
  does not fall as the table grows from 10 to 200 commands. The small student (LFM2.5-8B-A1B)
  manages 76% at 10 commands and 57% at 200.
- The large model runs on this laptop with its experts in RAM: 2.7 GB of VRAM gives 32 tokens per
  second, and 6.8 GB gives 40.
- The teacher is stable (299 of 300 labels the same when asked twice), but the right answer for a
  line changes as commands are added, so a label only holds for the table it was made against.
- Both students sometimes run a command when the teacher says none fits, mostly with small tables.
  Some of those are destructive (`proc.kill`, `fs.move`).

### Teacher (Claude Opus 5.5 through the `claude` CLI)

- 600 calls in two independent runs, median 3.4 seconds each, none needing a retry.
- The two runs chose the same command on 299 of 300 line-and-size pairs. The exception is
  "am I online?" at 50 commands (`net.speed` once, `net.ping` once).
- The teacher matched the author's intended command on 97 of 100 lines at 200 commands.
- **The right answer depends on the table.** 29 of the 100 lines change label between 10 and 200
  commands. Most go from `none` to a command that now exists. Three move from one command to a more
  specific one: battery (`sys.status` to `power.battery`), the to-do item (`note.add` to
  `todo.add`) and the tea timer (`remind.add` to `time.timer`).
- **A catch-all command hides a gap.** "how do I exit vim" is `none` until `web.search` exists,
  then it is dispatched there instead of being queued as a new need.

### Student: LFM2.5-8B-A1B (Q4_K_M, 5.2 GB, 32 experts with 4 used per token)

All weights on the GPU (5.5 GB of VRAM in use):

| Commands in table | 10 | 50 | 200 |
|---|---|---|---|
| Same command as teacher | 76% | 78% | 57% |
| Teacher picked a command: student picked another | 5% | 17% | 29% |
| Teacher picked a command: student said none | 5% | 1% | 20% |
| Teacher said none: student ran a command | 46% | 33% | 7% |
| Same command: same parameter names and values | 56% | 61% | 53% |
| Median seconds per line | 0.28 | 0.30 | 0.27 |

- **It does not hold as the table grows.** Agreement falls to 57% at 200 commands. The model picks
  near misses (`http.get` for `net.fetch`, `cal.add` for `remind.add`) and answers `none` to plain
  requests such as "force quit slack".
- **With a small table it runs a command when none fits.** At 10 commands, "delete the tmp
  directory" became `fs.move` and "write me a poem about my cat" became `note.add`.
- **It is repeatable on one placement.** Two runs gave identical answers on all 300.
- **Experts in RAM cost speed here.** With `--cpu-moe` the GPU holds 1.1 GB instead of 5.5 GB, and
  speed drops from about 200 to about 55 output tokens per second (1.2 to 1.5 seconds per line).
- **Placement changes answers.** Same model, same seed: with experts in RAM it picked the same
  command as on the GPU on 98, 96 and 84 of 100 lines at 10, 50 and 200 commands.
- **Teacher examples help.** With every fifth line and the teacher's answer added to the prompt,
  agreement on the other 80 lines went from 78% to 79%, 80% to 86% and 52% to 75%.

### Student: Gemma 4 26B-A4B (QAT q4_0, 14.4 GB, 128 experts with 8 used per token)

Placement left to llama.cpp, which kept the experts of about 20 of 30 layers in RAM (6.8 GB of
VRAM in use):

| Commands in table | 10 | 50 | 200 |
|---|---|---|---|
| Same command as teacher | 91% | 94% | 95% |
| Teacher picked a command: student picked another | 2% | 1% | 2% |
| Teacher picked a command: student said none | 5% | 4% | 4% |
| Teacher said none: student ran a command | 13% | 8% | 0% |
| Same command: same parameter names and values | 81% | 81% | 82% |
| Median seconds per line | 1.04 | 1.17 | 1.08 |

- **It holds as the table grows.** Agreement does not fall between 10 and 200 commands.
- **Its remaining errors at 200 are mostly defensible.** Of five, two are judgment calls (`fs.find`
  for "biggest files" where the teacher chose `fs.list`; `none` for "how do I exit vim" where the
  teacher chose `web.search`), one is a less specific command (`sys.status` for battery), and two
  are missed notes.
- **With a small table it still sometimes runs the wrong command.** At 10 commands, "what's
  listening on port 5432" became `proc.kill` with target 5432, and "make a backup copy of
  config.yaml" became `fs.move`.
- **Argument names are a weak spot.** Several times it used the value as the parameter name
  (`{"*.pdf": "*.pdf"}` for `{"name": "*.pdf"}`). The schema leaves parameter names as free strings;
  restricting them to the chosen command's parameters would rule this out.
- **It is repeatable.** Two runs on the same placement gave identical answers on all 300.
- **Placement barely changes its answers.** With every expert in RAM it scored the same 91%, 94% and
  95%, and picked the same command as the default placement on 299 of 300.
- **Teacher examples add little.** On the 80 lines not used as examples, agreement went from 94% to
  95%, 96% to 96% and 95% to 98%.

#### Placement on this laptop (RTX 3070 Laptop 8 GB, Ryzen 7 5800H, 30 GB RAM)

Measured with `scripts/placement-sweep.py`, 200-command table:

| Expert layers in RAM (of 30) | VRAM, MiB | Prompt tokens/s | Output tokens/s | Seconds per line |
|---|---|---|---|---|
| 30 | 2735 | 270 | 32 | 1.45 |
| 26 | 4369 | 310 | 35 | 1.34 |
| 22 | 6003 | 356 | 38 | 1.23 |
| 20 | 6819 | 384 | 40 | 1.15 |
| 18 | 7637 | 417 | 42 | 1.09 |
| chosen by llama.cpp | 6813 | 382 | 40 | 1.16 |

With every expert in RAM the model needs 2.7 GB of VRAM and still produces 32 tokens per second.
Filling the rest of the GPU with expert layers adds about 30%.

### The shell's dispatcher, replayed (Gemma 4 26B-A4B)

`los/dispatch.py` uses the same instructions with a stricter schema: each command only accepts its
own parameter names. `replay-shell.py` ran it over the same lines and tables:

| Commands in table | 10 | 50 | 200 |
|---|---|---|---|
| Same command as teacher | 91% | 95% | 94% |
| Teacher said none: student ran a command | 8% | 4% | 0% |
| Same command: same parameter names | 98% | 94% | 92% |
| Same command: same parameter names and values | 80% | 85% | 82% |

Against the looser schema above, command agreement is unchanged within one line per table,
parameter names improved (from 95%, 92% and 86%), and it ran a command the teacher would not have
less often (from 13%, 8% and 0%). It answered `none` a little more often at 10 commands (6 lines
where the teacher picked a command, up from 3).

### Search over server settings, 2026-10-06 (Gemma 4 26B-A4B)

`scripts/tune-server.py` started the server six ways and replayed the 100 lines at 200 commands
through the shell's dispatcher. A setting is accepted only if all 100 answers match the record
above and at least 500 MiB of GPU memory stays free, and it replaces the best so far only if it
is at least 5% faster.

| Extra server arguments | Seconds per line | Tokens per second | GPU MiB free | Verdict |
|---|---|---|---|---|
| (defaults) | 1.04 | 40 | 1027 | accepted |
| `--load-mode none` | 1.04 | 40 | 1011 | accepted |
| `--threads 12` | 1.10 | 37 | 1027 | accepted |
| `--threads 16` | 1.23 | 32 | 1027 | accepted |
| `--cpu-moe` | 1.27 | 32 | 5105 | accepted |
| `--fit-target 512` | 1.01 | 41 | 619 | 4 of 100 answers differ from the record |

- **Nothing beat the defaults.** The search found no improvement on this machine.
- **`--load-mode none` made no difference**, although llama.cpp suggests it at startup for this
  placement.
- **More threads than physical cores was slower.** The machine has 8 cores and 16 threads.
- **The one faster setting changed answers.** Letting llama.cpp put more expert layers on the GPU
  was 3% faster and changed 4 of 100 answers, so it was rejected.
- **The defaults reproduced the record exactly**, a day later and after a restart.

## Caveats

- One author wrote both the commands and the lines, so the lines may be easier than real use.
- Argument values are compared as lower-cased strings, so "2G" and "2 GB" count as different.
- The teacher is a reference, not ground truth: on ambiguous lines it has a defensible answer, not
  the only one.
