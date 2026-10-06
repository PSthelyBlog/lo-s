# Restart experiment

**Questions.** Does the model server give the same answers after a restart? If not, what changes
them, and what makes them the same again?

The machine experiment ended on a fault: after a restart of the server, the last step of
`sys.health` decoded wrongly for an input it had decoded correctly before. The cause was a guess.

## Method

- **Cases.** Recorded inputs with a recorded answer: the 100 dispatch lines at 200 commands, 50
  dispatch lines at 10 commands, and the 16 inputs the steps of `sys.health` have been decoded
  for, the four pinned inputs of its last step among them.
- **Conditions.** The server is started with nothing fixed, so that llama.cpp splits the weights
  between RAM and GPU itself, or with a fixed split. The GPU is otherwise free, or another program
  holds some of its memory (`hold-gpu.py`, which takes about 140 MiB more than it is asked for).
- **Cached replay.** Every start is asked the lines at 200 commands and the decodes, in the same
  order, with the server's prompt cache on. This is how the shell asked until now. The first
  start is then asked the same cases in reverse order.
- **Fresh replay.** Every start is asked the lines at 10 commands and the decodes with the prompt
  cache off, so the server works each prompt out from its first token. Odd starts ask in order
  and even starts in reverse.
- **The fixed split** is the one the first start chose: the same layers on the GPU and the same
  tensors kept in RAM, read from the server's debug log and given back as arguments.
- Every answer and every start is saved in `results/`.

## Running it

It starts its own servers on port 8091, so stop any other server that uses the GPU first.

```
experiments/restart/restart.py auto --starts 2 --reversed
experiments/restart/restart.py auto-held-500 --starts 1 --hold 500
experiments/restart/restart.py auto-held-1500 --starts 1 --hold 1500
experiments/restart/restart.py fixed --starts 3 --fixed
experiments/restart/restart.py fixed-held-500 --starts 1 --fixed --hold 500
experiments/restart/restart.py fixed-held-1500 --starts 1 --fixed --hold 1500
experiments/restart/restart.py --report        # the report again, from the saved answers
```

The whole run took 47 minutes.

## Results, 2026-10-06 (Gemma 4 26B-A4B, llama.cpp b11146, RTX 3070 Laptop 8 GB)

In short:

- **Two things changed answers: where the weights sat, and what the server had been asked
  before.** A restart by itself changed nothing.
- **The split follows the GPU memory free at start.** With the GPU free, llama.cpp kept the
  expert weights of layers 10 to 29 in RAM. With another program holding 640 MiB it kept those of
  layers 8 to 29, and with 1640 MiB held, those of layers 6 to 29.
- **Another split gave other answers**: 4 and 6 of 116 with the cache on, 1 and 2 of 66 with it
  off.
- **The fault of the machine experiment came back exactly.** With 1640 MiB held, the last step of
  `sys.health` decoded "memory fine, temperature worrying" as the instruction's own wording. So
  its cause was the split.
- **The order of the questions changed answers too.** Within one start, with one split, 5 of 116
  cached answers differed when the cases were asked in reverse. One line went from `fs.move` to
  no command at all.
- **A fixed split gave the first start's answers every time**: three starts, and a fourth with
  640 MiB held by another program, 182 answers each, none different.
- **With 1640 MiB held, the fixed split did not start.** The server stopped after two seconds
  with an out-of-memory error. It did not start with another split.
- **Fresh answers did not depend on order.** Six starts with the same split, asked in order or in
  reverse, gave the same 66 answers.
- **A fresh answer costs about 1.3 seconds more**: 3.24 seconds for a decode against 1.96, and
  2.41 for a dispatch line at 10 commands against 1.12.

### Each start

Answers are compared with those of the first start: nothing fixed, GPU otherwise free.

| Condition | Start | GPU MiB free before the server | Weights on the GPU, MiB | Same split as the first | Cached answers that differ | Fresh answers that differ |
|---|---|---|---|---|---|---|
| auto | 1 | 7562 | 5580 | yes | 0 of 116 | 0 of 66 |
| auto | 2 | 7562 | 5580 | yes | 0 of 116 | 0 of 66 |
| auto-held-500 | 1 | 6919 | 4763 | no | 4 of 116 | 1 of 66 |
| auto-held-1500 | 1 | 5919 | 3947 | no | 6 of 116 | 2 of 66 |
| fixed | 1 | 7562 | 5580 | yes | 0 of 116 | 0 of 66 |
| fixed | 2 | 7562 | 5580 | yes | 0 of 116 | 0 of 66 |
| fixed | 3 | 7562 | 5580 | yes | 0 of 116 | 0 of 66 |
| fixed-held-500 | 1 | 6919 | 5580 | yes | 0 of 116 | 0 of 66 |
| fixed-held-1500 | 1 | 5919 | did not start | | | |

The first start's cached answers are also the ones the earlier experiments recorded, all 116.
Its fresh answers differ from the record on one dispatch line, which was recorded with the cache
on.

### The same start, asked in another order

| Condition | Start | Cached answers that differ when asked in reverse |
|---|---|---|
| auto | 1 | 5 of 116 |

The server keeps what it worked out for earlier prompts and reuses the part a new prompt shares
with them. How much it reuses depends on what it was asked before. In a short trial that was not
saved, one line got three different answers within one start, as the server worked out 16, 5
and 17 tokens of the same prompt. Why that changes the answer was not measured. The likely reason
is that the same sums done in pieces of another size round differently.

### What a fresh answer costs

| Case | Median seconds, cached | Median seconds, fresh |
|---|---|---|
| decode | 1.96 | 3.24 |
| dispatch, 10 commands | 1.12 | 2.41 |
| dispatch, 200 commands | 1.05 | not measured |

The cached time for 10 commands is the one the dispatch experiment recorded for the same lines.
The lines at 200 commands were not asked fresh: their prompt is 3,000 tokens and takes about nine
seconds to work out.

### Every case that got another answer somewhere

- dispatch, 200 commands, line 1 (cached)
  - first start: `["fs.find", {"path": "downloads", "larger_than": "0"}]`
  - `["fs.find", {"path": "downloads", "larger_than": ""}]`: auto 1, reversed; auto-held-1500 1; auto-held-500 1
- dispatch, 200 commands, line 2 (cached)
  - first start: `["fs.find", {"path": ".", "older_than": "1 year"}]`
  - `["fs.find", {"path": ".", "name": "*.pdf", "older_than": "1 year"}]`: auto 1, reversed; auto-held-500 1
- dispatch, 200 commands, line 3 (cached)
  - first start: `["fs.find", {"name": "budget-2025"}]`
  - `["fs.find", {"path": ".", "name": "budget-2025"}]`: auto-held-1500 1
- dispatch, 200 commands, line 6 (cached)
  - first start: `["fs.find", {"path": "/", "name": "*.mkv"}]`
  - `["fs.find", {"path": "/", "name": ".mkv"}]`: auto 1, reversed; auto-held-500 1
- dispatch, 200 commands, line 16 (cached)
  - first start: `["fs.move", {"source": "site", "dest": "old-site"}]`
  - `["none", {}]`: auto 1, reversed; auto-held-1500 1
- dispatch, 200 commands, line 17 (cached)
  - first start: `["fs.move", {"source": "screenshots", "dest": "/mnt/usb/screenshots"}]`
  - `["fs.move", {"source": "screenshots", "dest": "/mnt/usb"}]`: auto-held-1500 1
- dispatch, 200 commands, line 28 (cached)
  - first start: `["proc.kill", {"target": "node server"}]`
  - `["proc.kill", {"target": "node"}]`: auto-held-1500 1
- dispatch, 200 commands, line 34 (cached)
  - first start: `["net.fetch", {"url": "https://example.net/report.pdf"}]`
  - `["net.fetch", {"url": "https://example.net/report.pdf", "output": "report.pdf"}]`: auto 1, reversed; auto-held-1500 1; auto-held-500 1
- dispatch, 10 commands, line 7 (fresh)
  - first start: `["fs.usage", {"path": "home"}]`
  - `["fs.usage", {"path": "home directory"}]`: auto-held-1500 1; auto-held-500 1
- decode, step 5, {"mem_level": "fine", "temp_level": "worrying"} (fresh)
  - first start: `{"op": "set", "register": "verdict", "value": "Worrying: {temp}"}`
  - `{"op": "set", "register": "verdict", "value": "Worrying: {mem} if mem_level is worrying and {temp} if temp_level is worrying, separated by a semicolon"}`: auto-held-1500 1

## What it means

- The split is now chosen once and repeated. `scripts/fix-split.py` reads from the server's log
  where llama.cpp put each tensor, and `scripts/serve.sh` starts the server with exactly that.
  When the split no longer fits, the server does not start.
- The local model is now asked with the prompt cache off (`extra` in `los.toml`). The cache is an
  optimization that changes answers, which the spec does not allow an optimization to do.
- With both, an answer depended on the request alone in every start measured here. A decode
  checked with `experiments/machine/step.py` is then the decode a run gets.
- The machine experiment's "identical inputs gave identical micro-ops" held because its runs asked
  in the same order every time.
- llama.cpp's own `llama-fit-params` tool was not used to choose the split. It prints arguments
  for a split, but estimates memory differently from the server and kept one more layer's experts
  in RAM than the server did.

## Caveats

- One machine, one model, one llama.cpp build. Another GPU, driver or build is another decoder,
  and nothing notices that yet.
- Few starts: two with nothing fixed, four with the fixed split, one for each held size.
- The same answer is not the right answer. The fixed split repeats the first start's answers,
  mistakes included.
- Fresh answers were checked on 66 cases, with prompts of 400 to 500 tokens.
- A model call on a new input now takes about 1.3 seconds longer: two thirds more for a decode,
  and more than double for a dispatch line. Whether the cache's speed can be had without its
  effect on answers was not tried.
- On a laptop where other programs use the GPU, the server will now sometimes refuse to start.
  The first start left 1,027 MiB free, so it starts as long as other programs take no more than
  about 1 GB beyond what they held when the split was chosen.
- Decodes remembered before this change were made with the cache on and under whatever split the
  server had. They are no longer used: the settings sent with a request are now part of what
  memory matches on.
- The run heats the machine: the GPU was at 72 °C early in it.
